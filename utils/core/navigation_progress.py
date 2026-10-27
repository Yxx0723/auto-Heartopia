"""Per-point drive budgets and position progress; independent of steering goals.

Pure engineering safeguards, not measured character capabilities. Callers account
only active navigation/recovery time, excluding menus, planning and pauses.
"""
import math

from utils.core import thresholds as policy


class NavigationProgress:
    def __init__(self):
        self.tag = None
        self.elapsed = self.motion_at = 0.
        self.anchor = None
        self.budget = 0.

    def bind(self, tag, position, target, speed):
        if self.tag == tag:
            return
        self.tag = tag
        self.elapsed = self.motion_at = 0.
        self.anchor = (position[0], position[2])
        speed = max(.5, min(10., speed))
        estimate = math.hypot(target[0]-position[0], target[2]-position[2])/speed
        self.budget = min(policy.COORD_NAV_POINT_DRIVE_MAX_SECONDS,
                          policy.COORD_NAV_POINT_DRIVE_BASE_SECONDS
                          + estimate*policy.COORD_NAV_POINT_DRIVE_FACTOR)

    def account(self, seconds):
        if self.tag is not None:
            self.elapsed += max(0., seconds)

    @property
    def exhausted(self):
        return self.tag is not None and self.elapsed >= self.budget

    def stalled(self, position):
        current = (position[0], position[2])
        if self.anchor is None or math.dist(current, self.anchor) >= policy.COORD_STUCK_DISTANCE:
            self.anchor, self.motion_at = current, self.elapsed
        return self.elapsed-self.motion_at >= policy.COORD_STUCK_SECONDS

    def begin_probe(self, position):
        # One deliberate new probe gets a fresh local observation window, but
        # never a new per-point time budget.
        self.anchor = (position[0], position[2])
        self.motion_at = self.elapsed
