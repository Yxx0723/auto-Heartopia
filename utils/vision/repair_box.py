"""Find the observed T-menu repair icon; no fixed desktop coordinates."""
from pathlib import Path
import math

import cv2
import numpy as np


def menu_scales(width, height):
    # Unity canvases may scale by width, height, or a logarithmic blend of both.
    # The reference was 4:3; a widescreen window needs all three hypotheses.
    x, y = width/1024., height/768.
    return sorted({round(base*factor, 5) for base in (y, math.sqrt(x*y), x)
                   for factor in (.9, .95, 1., 1.05, 1.1)})


class RepairBoxMenu:
    label = '维修盒'
    def __init__(self, template):
        data = np.frombuffer(Path(template).read_bytes(), dtype=np.uint8)
        self.template = cv2.imdecode(data, cv2.IMREAD_COLOR)
        marker = Path(template).with_name('repair_menu_marker.png')
        self.marker = cv2.imdecode(np.frombuffer(marker.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if self.template is None or self.template.shape[:2] != (43, 43) \
                or self.marker is None or self.marker.shape[:2] != (67, 65):
            raise ValueError('维修盒识别素材无效，请重新安装新版程序')
        self.tool_template = Path(template).with_name('axe_pick_icon.png')
        self.tool_verifier = None

    def locate(self, picture):
        frame = np.asarray(picture.convert('RGB'))
        height, width = frame.shape[:2]
        if not 64 <= width <= 16384 or not 64 <= height <= 16384:
            raise ValueError('维修盒识别画面尺寸无效')
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        # The user identified the BROWN box as the normal 3%/17s repair box.
        # Also require the held axe cell below it, independently of whether a
        # red advanced box is present. Matching the red box must never use it.
        left, top = width//2, height//2
        area = frame[top:, left:]
        matches = []
        tool_checked, tool_target = False, None
        for scale in menu_scales(width, height):
            tw, th = round(43*scale), round(43*scale)
            if tw < 24 or tw > area.shape[1] or th > area.shape[0]:
                continue
            template = cv2.resize(self.template, (tw, th), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            scores = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
            for _ in range(8):
                _, score, _, point = cv2.minMaxLoc(scores)
                if score < .93:
                    break
                px, py = point
                mw, mh = round(65*scale), round(67*scale)
                mx, my = px+round(2*scale), py+round(55*scale)
                marker = cv2.resize(self.marker, (mw, mh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
                padding = max(2, round(2*scale))
                x0, y0 = max(0, mx-padding), max(0, my-padding)
                patch = area[y0:min(area.shape[0], my+mh+padding), x0:min(area.shape[1], mx+mw+padding)]
                verified = False
                if patch.shape[0] >= mh and patch.shape[1] >= mw:
                    marker_score = cv2.minMaxLoc(cv2.matchTemplate(patch, marker, cv2.TM_CCOEFF_NORMED))[1]
                    verified = marker_score >= .87
                if not verified and score >= .95:
                    # The whole tool-cell marker changes with selection badges
                    # and UI scaling. Independently verify the masked axe and
                    # its tablet neighbour, then require their exact relation
                    # to this brown box. Never accept the box icon alone.
                    if not tool_checked:
                        from utils.vision.axe_pick import AxePickMenu
                        if self.tool_verifier is None:
                            self.tool_verifier = AxePickMenu(self.tool_template)
                        tool_target = self.tool_verifier.locate(picture)
                        tool_checked = True
                    tolerance = max(3, round(5*scale))
                    verified = tool_target is not None \
                        and abs(tool_target['x']-left-px-33*scale) <= tolerance \
                        and abs(tool_target['y']-top-py-90*scale) <= tolerance
                if verified:
                    matches.append((score, left+px+round(22*scale), top+py+round(20*scale), scale))
                scores[max(0, py-th):py+th, max(0, px-tw):px+tw] = -1
        if not matches:
            return None
        matches.sort(reverse=True)
        best = matches[0]
        if any(abs(m[1]-best[1]) > 8 or abs(m[2]-best[2]) > 8 for m in matches):
            raise ValueError('维修盒图标位置存在歧义，已取消自动点击')
        return {'x': best[1], 'y': best[2], 'score': best[0]}

    def prepare(self, window, tap, wait, check):
        check()
        first = self.locate(window.snapshot())
        if first is None:
            tap('t')
            wait(.4)
            check()
            first = self.locate(window.snapshot())
        if first is None:
            raise ValueError(f'未识别到 T 面板的{self.label}，请确认入口后按 F6 重试')
        wait(.12)
        check()
        second = self.locate(window.snapshot())
        if second is None or abs(first['x']-second['x']) > 3 or abs(first['y']-second['y']) > 3:
            raise ValueError(f'{self.label}图标位置不稳定，请关闭其他界面后按 F6 重试')
        return second

    @staticmethod
    def use(target, device, check, wait):
        check()
        try:
            device.execute({'type': 'mouse_down', 'button': 'left', 'x': target['x'], 'y': target['y']})
            wait(.1)  # Keep the press across a rendered game frame.
        finally:
            device.execute({'type': 'mouse_up', 'button': 'left'})

    def close_menu(self, window, tap, check, wait):
        check()
        if self.locate(window.snapshot()) is not None:
            tap('t')
            wait(.25)
            check()
            if self.locate(window.snapshot()) is not None:
                tap('esc')
                wait(.25)
                check()
                if self.locate(window.snapshot()) is not None:
                    raise ValueError('T 面板未关闭，请手动关闭后按 F6 继续')
