"""Select the fishing rod through its T-menu icon; no game memory writes.

The rod is the only tool whose tile never sits next to an empty tablet, so the
axe-pick matcher's "neighbouring tablet" proof cannot be reused here. Instead
this locator relies on the rod glyph itself: a masked ``TM_SQDIFF_NORMED``
match that must be clearly better than every other position in the panel.
An ambiguous frame raises, and the caller then keeps the manual prompt, so an
unverified coordinate is never clicked.

The artwork ``fishing_rod_icon.png`` is captured from the running game
(46x45, tight crop of the glyph). While it is missing the caller falls back to
the manual "please hold the rod" prompt.
"""
from pathlib import Path

import cv2
import numpy as np

from utils.vision.repair_box import RepairBoxMenu, menu_scales

#: A true rod tile scored 0.0055 in the live capture; 0.03 still rejects the
#: neighbouring axe and watering-can tiles by a wide margin.
TILE_MIN_SCORE = .03
#: A second location this much worse would mean two plausible rod tiles.
TILE_RIVAL_RATIO = 2.2
#: Peaks closer than this are the same tile seen at neighbouring scales.
TILE_SAME_RADIUS = 12.


class FishingRodMenu(RepairBoxMenu):
    label = '鱼竿'

    def __init__(self, template):
        data = np.frombuffer(Path(template).read_bytes(), dtype=np.uint8)
        self.template = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if self.template is None or self.template.shape[:2] != (46, 45):
            raise ValueError('鱼竿识别素材无效，请重新安装新版程序')
        # The pale tile background differs between selected and idle states;
        # only the rod line art and its float are stable.
        self.mask = (self.template.max(axis=2) < 200).astype(np.uint8)*255
        if np.count_nonzero(self.mask) < 100:
            raise ValueError('鱼竿图标特征不足')

    def locate(self, picture):
        rgb = np.asarray(picture.convert('RGB'))
        height, width = rgb.shape[:2]
        if not 64 <= width <= 16384 or not 64 <= height <= 16384:
            raise ValueError('鱼竿识别画面尺寸无效')
        frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        # The tool panel always renders in the lower-right quadrant.
        left, top = width//2, height//2
        area = frame[top:, left:]
        peaks = []
        for scale in menu_scales(width, height):
            tw, th = round(45*scale), round(46*scale)
            if tw < 16 or tw > area.shape[1] or th > area.shape[0]:
                continue
            interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
            template = cv2.resize(self.template, (tw, th), interpolation=interpolation)
            mask = cv2.resize(self.mask, (tw, th), interpolation=cv2.INTER_NEAREST)
            scores = cv2.matchTemplate(area, template, cv2.TM_SQDIFF_NORMED, mask=mask)
            scores = np.nan_to_num(scores, nan=1., posinf=1., neginf=1.)
            for _ in range(4):
                score, _, point, _ = cv2.minMaxLoc(scores)
                if score > TILE_MIN_SCORE:
                    break
                peaks.append((score, left+point[0]+tw//2, top+point[1]+th//2))
                scores[max(0, point[1]-th):point[1]+th, max(0, point[0]-tw):point[0]+tw] = 1.
        if not peaks:
            return None
        peaks.sort()
        best = peaks[0]
        rival = next((p for p in peaks[1:]
                      if abs(p[1]-best[1]) > TILE_SAME_RADIUS or abs(p[2]-best[2]) > TILE_SAME_RADIUS), None)
        if rival is not None and rival[0] < best[0]*TILE_RIVAL_RATIO:
            raise ValueError('鱼竿图标位置存在歧义，已取消自动点击')
        return {'x': best[1], 'y': best[2], 'score': 1.-best[0]}


def rod_template(resources):
    """Path of the captured rod glyph, or ``None`` when the artwork is absent."""
    candidate = Path(resources) / 'fishing_rod_icon.png'
    return candidate if candidate.exists() else None
