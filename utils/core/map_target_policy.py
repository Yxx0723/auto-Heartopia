"""UI engineering bounds. Only the 1 s hold trigger comes from game source.

Other durations, margins and retry limits are guards, not game-measured movement
abilities. They neither extend collision freshness nor change saved point XYZ.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MapTargetPolicy:
    hold_seconds: float = 1.1
    press_seconds: float = .08
    settle_seconds: float = .05
    action_timeout: float = 6.
    setup_timeout: float = 40.
    max_zoom_steps: int = 24
    view_max_age: float = 1.
    pixel_margin: float = 6.
    world_tolerance: float = .75

    def __post_init__(self):
        for name in ('hold_seconds','press_seconds','settle_seconds','action_timeout',
                     'setup_timeout','view_max_age','world_tolerance'):
            value=getattr(self,name)
            if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
                raise ValueError('地图操作时间或容差必须是有限正数')
        if type(self.max_zoom_steps) is not int or not 0<=self.max_zoom_steps<=100:
            raise ValueError('地图缩放次数无效')
        if type(self.pixel_margin) not in (int,float) or not math.isfinite(self.pixel_margin) \
                or self.pixel_margin<0:
            raise ValueError('地图点击边距无效')
