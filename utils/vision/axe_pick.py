"""Select the axe-pick through its T-menu icon; no game memory writes."""
from pathlib import Path

import cv2
import numpy as np

from utils.vision.repair_box import RepairBoxMenu, menu_scales


class AxePickMenu(RepairBoxMenu):
    label = '斧镐'

    def __init__(self, template):
        self.template = cv2.imdecode(np.frombuffer(Path(template).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        marker = Path(template).with_name('tool_tablet_marker.png')
        self.marker = cv2.imdecode(np.frombuffer(marker.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if self.template is None or self.template.shape[:2] != (46, 45) \
                or self.marker is None or self.marker.shape[:2] != (46, 45):
            raise ValueError('斧镐识别素材无效，请重新安装新版程序')
        # Ignore the pale tile background and selected border. The dark blade
        # and shaft are the same whether the tool is held or put away.
        self.mask = (self.template.max(axis=2) < 200).astype(np.uint8)*255
        if np.count_nonzero(self.mask) < 100:
            raise ValueError('斧镐图标特征不足')

    def locate(self, picture):
        target = self._locate_frame(picture)
        if target is None and picture.width > 1024:
            # A low-resolution UI render can be enlarged into a larger game
            # client. Its fine tablet details differ from a native-size icon.
            # Recheck the same captured frame at the verified small-client
            # width, then map the result back to actual client coordinates.
            width = 778
            height = round(picture.height*width/picture.width)
            normalized = picture.resize((width, height))
            target = self._locate_frame(normalized)
            if target is not None:
                target = {**target, 'x': round(target['x']*picture.width/width),
                          'y': round(target['y']*picture.height/height)}
        return target

    def _locate_frame(self, picture):
        rgb = np.asarray(picture.convert('RGB'))
        height, width = rgb.shape[:2]
        if not 64 <= width <= 16384 or not 64 <= height <= 16384:
            raise ValueError('斧镐识别画面尺寸无效')
        frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        left, top = width//2, height//2
        area, matches = frame[top:, left:], []
        for scale in menu_scales(width, height):
            tw, th = round(45*scale), round(46*scale)
            if tw < 16 or tw > area.shape[1] or th > area.shape[0]:
                continue
            interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
            template = cv2.resize(self.template, (tw, th), interpolation=interpolation)
            mask = cv2.resize(self.mask, (tw, th), interpolation=cv2.INTER_NEAREST)
            scores = cv2.matchTemplate(area, template, cv2.TM_SQDIFF_NORMED, mask=mask)
            scores = np.nan_to_num(scores, nan=1., posinf=1., neginf=1.)
            marker = cv2.resize(self.marker, (tw, th), interpolation=interpolation)
            for _ in range(8):
                score, _, point, _ = cv2.minMaxLoc(scores)
                if score > .045:
                    break
                px, py = point
                mx, my = px+round(68*scale), py+round(scale)
                padding = max(2, round(2*scale))
                patch = area[max(0, my-padding):min(area.shape[0], my+th+padding),
                             max(0, mx-padding):min(area.shape[1], mx+tw+padding)]
                if patch.shape[0] >= th and patch.shape[1] >= tw:
                    neighbour = cv2.minMaxLoc(cv2.matchTemplate(patch, marker, cv2.TM_CCOEFF_NORMED))[1]
                    # Downsampling a small tablet loses its fine screen detail.
                    # Accept that only alongside a stronger axe match; still
                    # require the independently positioned neighbour.
                    if neighbour >= .87 or (scale < .85 and score <= .025 and neighbour >= .74):
                        matches.append((1.-score, left+px+round(22*scale), top+py+round(23*scale)))
                scores[max(0, py-th):py+th, max(0, px-tw):px+tw] = 1.
        if not matches:
            return None
        matches.sort(reverse=True)
        best = matches[0]
        if any(abs(m[1]-best[1]) > 8 or abs(m[2]-best[2]) > 8 for m in matches):
            raise ValueError('斧镐图标位置存在歧义，已取消自动点击')
        return {'x': best[1], 'y': best[2], 'score': best[0]}


class LazyToolSelector:
    """No template loading or UI access until F6 starts the bound playback."""
    def __init__(self, template, factory=None):
        self.template, self.factory, self.menu = template, factory or AxePickMenu, None

    def __getattr__(self, name):
        if self.menu is None:
            self.menu = self.factory(self.template)
        return getattr(self.menu, name)
