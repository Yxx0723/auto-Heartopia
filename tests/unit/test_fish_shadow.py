"""fish_shadow 单测：合成鱼影图形，验证识别与过滤。"""
import cv2
import numpy as np
import pytest

from utils.vision import fish_shadow as fs


def make_water(w=1366, h=768, fish=False, fish2=False):
    """合成蓝色水面，可选放深色鱼影。"""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = (150, 100, 50)          # BGR: 中等蓝，V=150,S=200
    if fish:
        # 鱼影：BGR(46,21,0) V=46 S=255，纺锤形约 85x40
        cv2.ellipse(frame, (400, 250), (42, 20), -20, 0, 360, (46, 21, 0), -1)
        tri = np.array([[360, 250], [345, 238], [345, 262]], dtype=np.int32)
        cv2.fillPoly(frame, [tri], (46, 21, 0))
    if fish2:
        cv2.ellipse(frame, (700, 300), (35, 16), 30, 0, 360, (40, 18, 0), -1)
    return frame


def test_no_fish():
    assert fs.find_shadows(make_water()) == []


def test_detect_single_fish():
    shadows = fs.find_shadows(make_water(fish=True))
    assert len(shadows) == 1
    s = shadows[0]
    assert 300 <= s.area <= 8000
    assert 350 <= s.cx <= 450 and 200 <= s.cy <= 300


def test_detect_two_fish_sorted_by_area():
    shadows = fs.find_shadows(make_water(fish=True, fish2=True))
    assert len(shadows) == 2
    assert shadows[0].area >= shadows[1].area


def test_out_of_roi_ignored():
    """顶部 UI 区与下方栈道区的暗块不应被识别。"""
    frame = make_water()
    cv2.rectangle(frame, (200, 20), (260, 60), (30, 15, 0), -1)    # 顶部 UI
    cv2.rectangle(frame, (200, 600), (300, 700), (30, 15, 0), -1)  # 下方栈道
    assert fs.find_shadows(frame) == []


def test_light_object_ignored():
    """水面上的浅色涟漪/白点不应被识别。"""
    frame = make_water()
    cv2.circle(frame, (500, 250), 40, (220, 220, 220), -1)
    assert fs.find_shadows(frame) == []


def test_nearest_shadow():
    frame = make_water(fish=True, fish2=True)
    near = fs.nearest_shadow(frame, target=(400, 250))
    assert near is not None
    assert abs(near.cx - 400) < abs(near.cx - 700)


def test_nearest_none_when_empty():
    assert fs.nearest_shadow(make_water()) is None


def test_scaled_frame():
    """非参考尺寸（外框截图）也能识别。"""
    frame = make_water(fish=True)
    scaled = cv2.resize(frame, (1382, 807))
    shadows = fs.find_shadows(scaled)
    assert len(shadows) == 1


def test_empty_input():
    assert fs.find_shadows(None) == []
