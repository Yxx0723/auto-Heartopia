"""Select ordinary fruit and the observed 食用 button in the backpack."""
from pathlib import Path

import cv2
import numpy as np

from utils.vision.repair_box import RepairBoxMenu


FRUITS = {'apple': '苹果', 'blueberry': '蓝莓', 'raspberry': '树莓', 'orange': '橘子'}
BACKPACK_KEY = 'b'


class FruitMenu(RepairBoxMenu):
    label = '水果'

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
            for _ in range(8):
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
        return matches[0] if matches else None

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

    def prepare(self, window, tap, wait, check, device):
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

    def close_target(self, picture):
        frame = self.frame(picture)
        bag = self.bag_in(frame)
        if bag is None:
            return None
        scale = bag['scale']
        targets = self.matches(frame, self.close_icon, self.scales(frame),
                               left=max(0, round(bag['x']-30*scale)))
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
        if self.locate(picture) is None:
            return False
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
