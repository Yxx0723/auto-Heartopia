"""鱼影视觉识别：从游戏画面中找水面下的深色鱼剪影。

实测（2026-11-22）：鱼影 BGR≈(46,21,0)、HSV V≈46、S=255，水面 V≈145。
用 V 阈值 + 高饱和在 ROI 内找暗连通域，按面积过滤。ROI 排除顶部 UI 与木栈道。
纯函数，不读进程、不截屏、可单测。
"""
from dataclasses import dataclass

import cv2
import numpy as np

# HSV 阈值：鱼影是深蓝黑（低亮度、高饱和）
SHADOW_MAX_V = 75
SHADOW_MIN_S = 100
# 面积范围（像素）：实测一条鱼 2353px；留余量覆盖远近大小
SHADOW_MIN_AREA = 300
SHADOW_MAX_AREA = 8000
# 水面搜索 ROI（相对 1366x768 客户区）：排除顶部 UI、右侧图标、下方栈道
WATER_ROI = (100, 100, 1050, 440)


@dataclass(frozen=True)
class Shadow:
    area: int
    x: int
    y: int
    w: int
    h: int
    cx: float
    cy: float
    angle: float       # 鱼身主轴角度（度），0=水平，90=垂直

    @property
    def center(self):
        return (round(self.cx), round(self.cy))


def find_shadows(frame, roi=WATER_ROI, max_v=SHADOW_MAX_V, min_s=SHADOW_MIN_S,
                 min_area=SHADOW_MIN_AREA, max_area=SHADOW_MAX_AREA):
    """在 BGR frame 中找鱼影。frame 可为任意大小，roi 按比例缩放。"""
    if frame is None or getattr(frame, 'size', 0) == 0:
        return []
    h, w = frame.shape[:2]
    # ROI 按参考尺寸 1366x768 等比映射到实际帧
    x0, y0, x1, y1 = roi
    sx, sy = w / 1366.0, h / 768.0
    ax0, ay0 = int(x0 * sx), int(y0 * sy)
    ax1, ay1 = min(w, int(x1 * sx)), min(h, int(y1 * sy))
    if ax1 <= ax0 or ay1 <= ay0:
        return []
    crop = frame[ay0:ay1, ax0:ax1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    V, S = hsv[:, :, 2], hsv[:, :, 1]
    mask = ((V < max_v) & (S >= min_s)).astype(np.uint8) * 255
    # 面积按缩放平方调整
    scale_area = sx * sy
    lo = int(min_area * scale_area)
    hi = int(max_area * scale_area)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if not lo <= area <= hi:
            continue
        pts = np.column_stack(np.where(labels == i))   # (row, col)
        angle = 0.0
        if len(pts) >= 5:
            rect = cv2.minAreaRect(pts[:, ::-1].astype(np.float32))
            angle = float(rect[2])
            if rect[1][0] < rect[1][1]:
                angle += 90.0
        out.append(Shadow(area, ax0 + x, ay0 + y, bw, bh,
                          ax0 + float(cents[i][0]), ay0 + float(cents[i][1]), angle))
    out.sort(key=lambda s: -s.area)
    return out


def nearest_shadow(frame, target=None, **kwargs):
    """返回离 target (x,y) 最近的鱼影；target 缺省为画面中心。"""
    shadows = find_shadows(frame, **kwargs)
    if not shadows:
        return None
    h, w = frame.shape[:2]
    tx, ty = target if target is not None else (w / 2, h / 2)
    return min(shadows, key=lambda s: (s.cx - tx) ** 2 + (s.cy - ty) ** 2)
