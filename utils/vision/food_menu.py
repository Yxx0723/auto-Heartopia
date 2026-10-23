"""Select inventory-confirmed food with stable cell, quality and Eat checks."""
from pathlib import Path
import random
import hashlib

import cv2
import numpy as np

from utils.vision.repair_box import RepairBoxMenu
from utils.core import thresholds as policy
from utils.core.food_policy import choose_foods


FRUITS = {'apple': '苹果', 'blueberry': '蓝莓', 'raspberry': '树莓', 'orange': '橘子'}
BACKPACK_KEY = 'b'


class FruitMenu(RepairBoxMenu):
    label = '食物'

    def __init__(self, template):
        root = Path(template).parent
        def read(name):
            result = cv2.imdecode(np.frombuffer((root/name).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            if result is None:
                raise ValueError('水果识别素材无效')
            return result
        self.bag = read('food_bag_marker.png')
        self.close_icon = read('food_bag_close.png')
        self.eat = read('food_eat_button.png')
        self.category = read('food_category.png')
        self.search_header = read('food_search_header.png')
        self.search_results = read('food_search_results.png')
        self.search_end = read('food_search_end.png')
        self.star_icon = read('food_star.png')
        self.fruits = {key: read(f'fruit_{key}.png') for key in FRUITS}
        if self.bag.shape[:2] != (28, 55) or self.close_icon.shape[:2] != (21, 21) or self.eat.shape[:2] != (18, 112) \
                or self.category.shape[:2] != (23, 23) or any(t.shape[:2] != (31, 31) for t in self.fruits.values()):
            raise ValueError('水果识别素材布局无效，请重新安装新版程序')

    @staticmethod
    def frame(picture):
        rgb = np.asarray(picture.convert('RGB'))
        if not 64 <= rgb.shape[0] <= 16384 or not 64 <= rgb.shape[1] <= 16384:
            raise ValueError('食物识别画面尺寸无效')
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    @staticmethod
    def scales(frame):
        height, width = frame.shape[:2]
        x, y = width/778., height/437.
        return sorted({round(base*v, 5) for base in (x, y, (x*y)**.5) for v in (.95, 1., 1.05)})

    @staticmethod
    def matches(frame, template, scales, left=0, top=0, masked=False, minimum=.92):
        area, found = frame[top:, left:], []
        for scale in scales:
            width, height = round(template.shape[1]*scale), round(template.shape[0]*scale)
            if min(width, height) < 10 or width > area.shape[1] or height > area.shape[0]:
                continue
            image = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            if masked:
                mask = cv2.resize((template.min(axis=2) < 170).astype(np.uint8)*255,
                                  (width, height), interpolation=cv2.INTER_NEAREST)
                scores = cv2.matchTemplate(area, image, cv2.TM_SQDIFF_NORMED, mask=mask)
                scores = 1.-np.nan_to_num(scores, nan=1., posinf=1., neginf=1.)
                threshold = .95
            else:
                scores = cv2.matchTemplate(area, image, cv2.TM_CCOEFF_NORMED)
                threshold = minimum
            for _ in range(20):
                _, score, _, (x, y) = cv2.minMaxLoc(scores)
                if score < threshold:
                    break
                shape_score = cv2.minMaxLoc(cv2.matchTemplate(area[y:y+height, x:x+width], image,
                                                              cv2.TM_CCOEFF_NORMED))[1] if masked else 1.
                if shape_score >= .88:
                    found.append({'x': left+x+width/2, 'y': top+y+height/2,
                                  'scale': scale, 'score': score})
                scores[max(0, y-height):y+height, max(0, x-width):x+width] = -1
        return sorted(found, key=lambda m: -m['score'])

    def bag_in(self, frame):
        matches = self.matches(frame, self.bag, self.scales(frame), left=frame.shape[1]//2, minimum=.90)
        if matches:
            return matches[0]
        results = self.matches(frame, self.search_results, self.scales(frame), left=frame.shape[1]//2)
        if results and self.end_search_target(frame):
            result = results[0]
            return dict(result, x=frame.shape[1]-55*result['scale'], search_results=True)
        return None

    def locate(self, picture):
        return self.bag_in(self.frame(picture))

    def available(self, frame, bag):
        scale = bag['scale']
        result = []
        for key, template in self.fruits.items():
            matches = self.matches(frame, template, self.scales(frame),
                                   left=max(0, round(bag['x']-240*scale)), top=max(0, round(bag['y']+25*scale)), masked=True)
            result.extend(dict(m, food=FRUITS[key]) for m in matches)
        return result

    @staticmethod
    def selected(frame, fruit):
        x, y, s = fruit['x'], fruit['y'], fruit['scale']
        radius = round(24*s)
        x0, y0 = round(x)-radius, round(y)-radius
        crop = frame[max(0, y0):round(y)+radius+1, max(0, x0):round(x)+radius+1]
        if crop.shape[:2] != (radius*2+1, radius*2+1):
            return False
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        yellow = (hsv[:, :, 0] >= 20) & (hsv[:, :, 0] <= 40) & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 170)
        inset = max(3, round(5*s))
        yellow[inset:-inset, inset:-inset] = False
        return np.count_nonzero(yellow) >= max(25, round(40*s*s))

    def ready(self, picture):
        frame = self.frame(picture)
        bag = self.bag_in(frame)
        if bag is None:
            return None
        selected = [f for f in self.available(frame, bag) if self.selected(frame, f)]
        if not selected:
            return None
        names = {f['food'] for f in selected}
        if len(names) != 1:
            raise ValueError('食物选中状态存在歧义')
        # The detail card follows the selected inventory cell and can extend
        # into the left half of the game. Hover also adds a pointer glyph at
        # the right of the button. Match the unchanged text, then independently
        # verify the cyan button and its position relative to the selected fruit.
        # Font rasterization varies across render resolutions. A text match is
        # only a candidate: the selected whitelisted fruit, neighbouring card
        # and the independently coloured button body are still mandatory.
        buttons = self.matches(frame, self.eat[:, 38:74], self.scales(frame), minimum=.88)
        buttons = [b for b in buttons if self.is_food_button(frame, b, selected)]
        if not buttons:
            return None
        best = buttons[0]
        if any(abs(b['x']-best['x']) > 8 or abs(b['y']-best['y']) > 8 for b in buttons):
            raise ValueError('食用按钮位置存在歧义')
        return dict(best, food=selected[0]['food'])

    @staticmethod
    def is_food_button(frame, button, selected):
        x, y, scale = button['x'], button['y'], button['scale']
        # The floating card can sit directly beside the selected cell or be
        # shifted when clamped inside the viewport. Both observed layouts must
        # remain near that cell; a fixed 250-unit offset rejects the near card.
        if not any(75*scale <= abs(f['x']-x) <= 272*scale and abs(f['y']-y) <= 180*scale
                   for f in selected):
            return False
        # Keep the full button body in bounds. Text alone or another panel's
        # similarly named action must never be sufficient to consume an item.
        left, right = round(x-55*scale), round(x+55*scale)
        top, bottom = round(y-9*scale), round(y+9*scale)
        if left < 0 or top < 0 or right > frame.shape[1] or bottom > frame.shape[0]:
            return False
        hsv = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2HSV)
        cyan = (hsv[:, :, 0] >= 75) & (hsv[:, :, 0] <= 100) & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 100)
        return bool(np.mean(cyan) >= .65)

    def prepare_visible(self, window, tap, wait, check, device):
        check()
        picture = window.snapshot()
        if self.locate(picture) is None:
            tap(BACKPACK_KEY)
            wait(.5)
            check()
            picture = window.snapshot()
        first = self.ready(picture)
        if first is None:
            frame = self.frame(picture)
            bag = self.bag_in(frame)
            if bag is None:
                raise ValueError('按 B 后未识别到背包，请确认背包快捷键并关闭其他界面后重试')
            fruits = self.available(frame, bag)
            if not fruits:
                # The last manually selected backpack category may hide fruit.
                # Match the agricultural-items icon in the header before input.
                header = frame[:round(bag['y']+20*bag['scale'])]
                tabs = self.matches(header, self.category, self.scales(frame), left=max(0, round(bag['x']-220*bag['scale'])))
                if tabs:
                    self.use(tabs[0], device, check, wait)
                    wait(.35)
                    check()
                    frame = self.frame(window.snapshot())
                    bag = self.bag_in(frame)
                    if bag is None:
                        raise ValueError('切换水果页后背包未确认')
                    fruits = self.available(frame, bag)
            if not fruits:
                raise ValueError('当前背包页没有可识别的苹果、蓝莓、树莓或橘子，请切到水果页')
            # available preserves the user's ordinary-fruit priority.
            fruit = fruits[0]
            self.use(fruit, device, check, wait)
            wait(.35)
            check()
            first = self.ready(window.snapshot())
            if first is None or first['food'] != fruit['food']:
                raise ValueError('水果选用未确认，未点击食用')
        wait(.12)
        check()
        second = self.ready(window.snapshot())
        if second is None or second['food'] != first['food'] or abs(first['x']-second['x']) > 3 \
                or abs(first['y']-second['y']) > 3:
            raise ValueError('食用按钮位置不稳定，未食用')
        return second

    def end_search_target(self, frame):
        targets = self.matches(frame, self.search_end, self.scales(frame),
                               left=frame.shape[1]//2, top=frame.shape[0]*3//4, minimum=.88)
        return targets[0] if targets else None

    def search_dialog(self, picture):
        frame = self.frame(picture)
        headers = self.matches(frame, self.search_header, self.scales(frame), minimum=.90)
        targets = []
        for header in headers:
            x, y, scale = header['x'], header['y'], header['scale']
            bx, by = x+118*scale, y+58*scale
            left, right = round(bx-32*scale), round(bx+32*scale)
            top, bottom = round(by-11*scale), round(by+11*scale)
            if left < 0 or top < 0 or right > frame.shape[1] or bottom > frame.shape[0]:
                continue
            hsv = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2HSV)
            cyan = (hsv[:, :, 0] >= 75) & (hsv[:, :, 0] <= 100) & (hsv[:, :, 1] >= 70)
            if np.mean(cyan) < .65:
                continue
            targets.append(dict(header, x=x-43*scale, y=y+58*scale,
                                submit={'x': bx, 'y': by},
                                close={'x': x+171*scale, 'y': y}))
        if not targets:
            return None
        first = targets[0]
        if any(abs(t['x']-first['x']) > 8 or abs(t['y']-first['y']) > 8 for t in targets):
            raise ValueError('背包搜索窗口存在歧义')
        return first

    def stable_dialog(self, window, wait, check):
        # The panel may animate/load for several frames after a registered
        # click. Require two consecutive stable observations, with a deadline.
        previous = None
        for _ in range(20):
            check()
            current = self.search_dialog(window.snapshot())
            if current is not None and previous is not None and abs(current['x']-previous['x']) <= 3 \
                    and abs(current['y']-previous['y']) <= 3:
                return current
            previous = current
            wait(.15)
        raise ValueError('背包搜索框未确认，未输入文字')

    def click_control(self, target, device, check, wait):
        check()
        device.execute({'type': 'cursor_move', 'x': target['x'], 'y': target['y']})
        wait(.15)  # Let the game process pointer arrival before button input.
        check()
        self.use(target, device, check, wait)

    def open_search(self, window, tap, wait, check, device):
        check()
        picture = window.snapshot()
        if self.search_dialog(picture) is not None:
            return self.stable_dialog(window, wait, check)
        frame = self.frame(picture)
        end = self.end_search_target(frame)
        if end is not None:
            self.use(end, device, check, wait)
            wait(.3)
            picture = window.snapshot()
        bag = self.locate(picture)
        if bag is None:
            tap(BACKPACK_KEY)
            wait(.5)
            check()
            picture = window.snapshot()
            bag = self.locate(picture)
        if bag is None or bag.get('search_results'):
            raise ValueError('按 B 后未确认完整背包，未打开搜索')
        target = dict(bag, x=bag['x']-13*bag['scale'])
        self.click_control(target, device, check, wait)
        wait(.3)
        return self.stable_dialog(window, wait, check)

    def ready_food(self, picture, food, cell):
        frame = self.frame(picture)
        if not self.selected(frame, cell):
            return None
        if food in FRUITS.values():
            target = self.ready(picture)
            return target if target is not None and target['food'] == food else None
        frame = self.frame(picture)
        bag = self.bag_in(frame)
        # Generic foods still require a filtered backpack, the selected cell
        # border, real 食用 text and the independently checked cyan button.
        if bag is None or not bag.get('search_results') or not self.selected(frame, cell):
            return None
        buttons = self.matches(frame, self.eat[:, 38:74], self.scales(frame), minimum=.88)
        buttons = [b for b in buttons if self.is_food_button(frame, b, [cell])]
        if not buttons:
            return None
        best = buttons[0]
        if any(abs(b['x']-best['x']) > 8 or abs(b['y']-best['y']) > 8 for b in buttons):
            raise ValueError('食用按钮位置存在歧义，未食用')
        return dict(best, food=food)

    def detail_stars(self, picture, button):
        """Count stars in the confirmed item's cream header, not grid order.

        Same-name search results can contain several quality variants. Require
        the actual detail header and reject ambiguous/unmatched gold marks.
        """
        frame = self.frame(picture)
        x, y, scale = button['x'], button['y'], button['scale']
        left, right = max(0, round(x-100*scale)), min(frame.shape[1], round(x+140*scale))
        top, bottom = max(0, round(y-185*scale)), max(0, round(y-30*scale))
        roi = frame[top:bottom, left:right]
        if not roi.size:
            return None
        blue, green, red = cv2.split(roi.astype(np.int16))
        cream = ((red >= 225) & (green >= 205) & (green <= 245) & (blue >= 175)
                 & (blue <= 220) & (red-green > 5) & (green-blue > 10)).astype(np.uint8)
        _, _, stats, _ = cv2.connectedComponentsWithStats(cream)
        headers = [r for r in stats[1:] if 110*scale <= r[2] <= 190*scale
                   and 15*scale <= r[3] <= 65*scale and r[4] >= 1800*scale**2]
        if len(headers) != 1:
            return None
        hx, hy, width, height, _ = headers[0]
        band = roi[max(0, round(hy+height-10*scale)):min(roi.shape[0], round(hy+height+10*scale)),
                   round(hx+5*scale):round(hx+width-5*scale)]
        if not band.size:
            return None
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        gold = (hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40) & (hsv[:, :, 1] >= 90) & (hsv[:, :, 2] >= 170)
        centers = []
        # The verified star capture is 15px at 1366px client width.
        for factor in (1., .96, 1.04):
            size = max(5, round(15*scale/(1366/778)*factor))
            if size > min(band.shape[:2]):
                continue
            icon = cv2.resize(self.star_icon, (size, size), interpolation=cv2.INTER_AREA)
            scores = cv2.matchTemplate(band, icon, cv2.TM_CCOEFF_NORMED)
            for _ in range(6):
                _, score, _, (sx, sy) = cv2.minMaxLoc(scores)
                if score < .82:
                    break
                center = (sx+size/2, sy+size/2)
                if not any(abs(center[0]-cx) < size*.65 and abs(center[1]-cy) < size*.65 for cx, cy in centers):
                    centers.append(center)
                scores[max(0, sy-size//2):sy+size//2+1, max(0, sx-size//2):sx+size//2+1] = -1
        if len(centers) > 5 or (centers and max(cy for _, cy in centers)-min(cy for _, cy in centers) > 3*scale):
            return None
        covered = np.zeros(gold.shape, dtype=bool)
        for cx, cy in centers:
            radius = round(5*scale)
            covered[max(0, round(cy)-radius):round(cy)+radius+1,
                    max(0, round(cx)-radius):round(cx)+radius+1] = True
        if np.count_nonzero(gold & ~covered) > max(3, 3*scale**2):
            return None
        return len(centers)

    @staticmethod
    def page_key(frame, bag):
        scale=bag['scale']
        left=max(0,round(bag['x']-238*scale));right=min(frame.shape[1],round(bag['x']+35*scale))
        top=max(0,round(bag['y']+20*scale));bottom=max(top,frame.shape[0]-round(30*scale))
        area=frame[top:bottom,left:right]
        if not area.size:raise ValueError('搜索结果格子区域未确认')
        return hashlib.sha256(area.tobytes()).digest()

    def result_pages(self, window, device, wait, check, food, query_check):
        seen=set()
        for page in range(policy.COORD_FOOD_SEARCH_MAX_PAGES):
            check()
            frame=self.frame(window.snapshot());bag=self.bag_in(frame)
            if bag is None or not bag.get('search_results') or self.end_search_target(frame) is None:
                raise ValueError('搜索结果页面未确认，未滚动或食用')
            if query_check is not None and query_check()!=food:
                raise ValueError('背包实际搜索文字不符，未选择食物')
            key=self.page_key(frame,bag)
            if key in seen:return
            seen.add(key)
            yield frame,bag
            if page+1>=policy.COORD_FOOD_SEARCH_MAX_PAGES:return
            check()
            # Scroll only inside the freshly confirmed filtered backpack grid.
            device.execute({'type':'cursor_move','x':bag['x']-20*bag['scale'],
                            'y':bag['y']+80*bag['scale']})
            wait(.15);check()
            current=self.frame(window.snapshot())
            if self.bag_in(current) is None or self.end_search_target(current) is None:
                raise ValueError('滚动前搜索结果已关闭，未滚动')
            if query_check is not None and query_check()!=food:
                raise ValueError('滚动前搜索文字已变化，未滚动')
            device.execute({'type':'scroll','dx':0,'dy':-3})
            wait(.35)

    def confirmed_cell(self, window, food, cell, wait, check, query_check=None, star=None):
        observations=[]
        for observation_index in range(2):
            check()
            if query_check is not None and query_check()!=food:
                raise ValueError('背包实际搜索文字不符，未选择食物')
            picture=window.snapshot()
            target=self.ready_food(picture,food,cell)
            if target is None:return None
            # The star belongs to this exact frame and selected cell, rather
            # than an additional screenshot captured after the detail changed.
            if star is not None and self.detail_stars(picture,target)!=star:return None
            observations.append(target)
            if observation_index == 0:
                wait(.12)
        first,second=observations
        if abs(first['x']-second['x'])>3 or abs(first['y']-second['y'])>3:return None
        return second

    def confirm(self, target, window, wait, check, query_check=None):
        cell=target.get('cell')
        if not isinstance(cell,dict):raise ValueError('选中食物格子未确认')
        result=self.confirmed_cell(window,target['food'],cell,wait,check,query_check,
                                   target.get('star') if target.get('requires_star') else None)
        if result is None:raise ValueError('食用前详情、按钮或星级已变化，未食用')
        return dict(target,**result)

    def prepare(self, window, tap, wait, check, device, inventory=None, query_check=None, fallback_mode='fruit'):
        pool=choose_foods(inventory,fallback_mode)
        if pool['state'] not in ('prepared','fallback'):
            raise ValueError(pool['reason'])
        prepared=pool['state']=='prepared'
        foods=sorted({item['name'] for item in pool['items']})
        for food in random.sample(foods,len(foods)):
            members=[item for item in pool['items'] if item['name']==food]
            requires_star=prepared or any(item.get('verify_star') for item in members)
            target_star=pool['star']
            if requires_star and target_star is None:
                stars=[item.get('star') for item in members]
                if any(type(v) is not int or not 0<=v<=5 for v in stars):
                    raise ValueError('同名锁定物品的目标星级未确认')
                target_star=min(stars)
            dialog = self.open_search(window, tap, wait, check, device)
            self.click_control(dialog, device, check, wait)
            if self.search_dialog(window.snapshot()) is None:
                raise ValueError('输入前搜索窗口已关闭，未输入文字')
            check()
            try:
                device.execute({'type': 'key_down', 'key': 'ctrl_l'})
                tap('a')
            finally:
                device.execute({'type': 'key_up', 'key': 'ctrl_l'})
            tap('backspace')
            device.type_text(food, check, wait)
            dialog = self.stable_dialog(window, wait, check)
            self.click_control(dialog['submit'], device, check, wait)
            for _ in range(20):
                wait(.15)
                check()
                picture = window.snapshot()
                frame = self.frame(picture)
                if self.search_dialog(picture) is None and self.end_search_target(frame) is not None \
                        and self.bag_in(frame) is not None:
                    break
            else:
                raise ValueError('未确认背包搜索结果，未选择食物')
            frame = self.frame(picture)
            bag = self.bag_in(frame)
            if bag is None:
                raise ValueError('搜索结果未确认')
            check()
            if query_check is not None and query_check() != food:
                raise ValueError('背包实际搜索文字不符，未选择食物')
            for frame,bag in self.result_pages(window,device,wait,check,food,query_check):
                if food in FRUITS.values():
                    cells = [f for f in self.available(frame, bag) if f['food'] == food]
                else:
                    scale = bag['scale']
                    # Visible filtered rows, anchored to the detected header,
                    # never to desktop resolution. Quality variants can span more
                    # than one row. Recipes are rejected
                    # by the selected outline + actual 食用 action below.
                    rows = max(0, min(12, int((frame.shape[0]-55*scale-bag['y']-46*scale)/(54*scale))+1))
                    cells = [dict(x=bag['x']+(54*i-211)*scale, y=bag['y']+(46+54*j)*scale,
                                  scale=scale, food=food) for j in range(rows) for i in range(5)]
                    cells = [c for c in cells if 0 < c['x'] < frame.shape[1] and 0 < c['y'] < frame.shape[0]]
                for cell in cells:
                    self.click_control(cell, device, check, wait)
                    wait(.35)
                    check()
                    confirmed=self.confirmed_cell(window,food,cell,wait,check,query_check,
                                                  target_star if requires_star else None)
                    if confirmed is not None:
                        proof={'cell':{k:v for k,v in cell.items() if k in ('x','y','scale')},
                               'prepared':prepared,'requires_star':requires_star}
                        if requires_star:proof['star']=target_star
                        return dict(confirmed,**proof)
        raise ValueError('背包已有食物的搜索结果或食用按钮未确认，未食用')

    def close_target(self, picture):
        frame = self.frame(picture)
        bag = self.bag_in(frame)
        if bag is None:
            return None
        scale = bag['scale']
        targets = self.matches(frame, self.close_icon, self.scales(frame),
                               left=max(0, round(bag['x']-30*scale)), minimum=policy.COORD_FOOD_CLOSE_MATCH)
        targets = [t for t in targets if abs(t['x']-bag['x']-13*scale) <= 10*scale
                   and abs(t['y']-bag['y']+28*scale) <= 10*scale]
        if not targets:
            return None
        best = targets[0]
        if any(abs(t['x']-best['x']) > 8 or abs(t['y']-best['y']) > 8 for t in targets):
            raise ValueError('背包关闭按钮存在歧义，未点击')
        return best

    def close_menu(self, window, tap, check, wait, device):
        # B opens the backpack but does not dismiss the main backpack panel.
        # Use its observed in-game close icon, located relative to the header,
        # never the native window close control or a guessed screen position.
        check()
        picture = window.snapshot()
        if self.locate(picture) is None and self.search_dialog(picture) is None:
            return False
        dialog = self.search_dialog(picture)
        if dialog is not None:
            dialog = self.stable_dialog(window, wait, check)
            self.use(dialog['close'], device, check, wait)
            wait(.3)
            check()
            picture = window.snapshot()
            if self.search_dialog(picture) is not None:
                raise ValueError('背包搜索窗口未关闭，未继续移动')
        end = self.end_search_target(self.frame(picture))
        if end is not None:
            self.use(end, device, check, wait)
            wait(.3)
            check()
            picture = window.snapshot()
            if self.end_search_target(self.frame(picture)) is not None:
                raise ValueError('背包搜索结果未退出，未继续移动')
        first = self.close_target(picture)
        if first is None:
            raise ValueError('未识别到背包关闭按钮，未点击')
        wait(.12)
        check()
        picture = window.snapshot()
        if self.locate(picture) is None:
            return True
        second = self.close_target(picture)
        if second is None or abs(second['x']-first['x']) > 3 or abs(second['y']-first['y']) > 3:
            raise ValueError('背包关闭按钮位置不稳定，未点击')
        self.use(second, device, check, wait)
        for _ in range(5):
            wait(.15)
            check()
            if self.locate(window.snapshot()) is None:
                return True
        raise ValueError('点击关闭后背包仍展开，请手动关闭后按 F6 继续')
