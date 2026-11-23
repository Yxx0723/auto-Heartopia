# -*- coding: utf-8 -*-
"""落点圈识别 v2：加"空心环"判据（圆周亮、内部暗），排除实心 UI 图标；
限制在水面中部区域（x 250~1050, y 140~460）。可指定上一帧位置做局部优先。
"""
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Ring:
    cx: float
    cy: float
    radius: float

    @property
    def center(self):
        return (round(self.cx), round(self.cy))


def _ring_score(gray, x, y, rad):
    """圆周亮、内部暗 → 空心环得分；实心图标内部也亮，得分低。"""
    angles = np.linspace(0, 2 * np.pi, 36, endpoint=False)
    h, w = gray.shape[:2]
    per = []
    for a in angles:
        px = int(round(x + rad * np.cos(a)))
        py = int(round(y + rad * np.sin(a)))
        if 0 <= px < w and 0 <= py < h:
            per.append(gray[py, px])
    if len(per) < 24:
        return 0.0
    per = np.array(per, dtype=np.float32)
    per_bright = float((per > 150).mean())
    # 内部采样
    inner = []
    for a in angles[::3]:
        px = int(round(x + rad * 0.45 * np.cos(a)))
        py = int(round(y + rad * 0.45 * np.sin(a)))
        if 0 <= px < w and 0 <= py < h:
            inner.append(gray[py, px])
    inner_bright = float((np.array(inner, dtype=np.float32) > 150).mean()) if inner else 1.0
    # 空心环：边缘亮、内部暗
    return per_bright * (1.0 - min(1.0, inner_bright * 1.6))


def find_ring(frame, prev=None, x_range=(250, 1060), y_range=(140, 470),
              min_radius=13, max_radius=42):
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 1.5)
    circles = cv2.HoughCircles(blurred, cv2.HOUGH_GRADIENT, dp=1.2, minDist=35,
                               param1=90, param2=18,
                               minRadius=min_radius, maxRadius=max_radius)
    if circles is None:
        return None
    best = None
    x0, x1 = x_range
    y0, y1 = y_range
    for x, y, rad in np.round(circles[0]).astype(int):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        score = _ring_score(gray, x, y, rad)
        if score < 0.18:
            continue
        if prev is not None:
            d = ((x - prev.cx) ** 2 + (y - prev.cy) ** 2) ** 0.5
            score *= max(0.3, 1.0 - d / 400.0)   # 局部优先
        if best is None or score > best[0]:
            best = (score, Ring(float(x), float(y), float(rad)))
    return best[1] if best else None
