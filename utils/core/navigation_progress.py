"""Per-point drive budgets and position progress; independent of steering goals.

Pure engineering safeguards, not measured character capabilities. Callers account
only active navigation/recovery time, excluding menus, planning and pauses.
"""
import math
from collections import deque
from utils.core.collection_schedule import LOOP_WINDOW_SECONDS

from utils.core import thresholds as policy


class NavigationProgress:
    def __init__(self):
        self.tag = None
        self.elapsed = self.motion_at = 0.
        self.anchor = None
        self.budget = 0.
        self.visits = deque(maxlen=64)

    def bind(self, tag, position, target, speed):
        if self.tag == tag:
            return
        self.tag = tag
        self.visits.clear()
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

    def looping(self, position):
        """Repeated returns with substantial travel, not ordinary slow walking."""
        if self.visits and self.elapsed-self.visits[-1][0]<.5:
            return False
        self.visits.append((self.elapsed,tuple(position)))
        while self.visits and self.elapsed-self.visits[0][0]>LOOP_WINDOW_SECONDS+3.:
            self.visits.popleft()
        if len(self.visits)<4 or self.elapsed-self.visits[0][0]<LOOP_WINDOW_SECONDS:
            return False
        visits=list(self.visits)
        returns=sum(math.hypot(position[0]-p[0],position[2]-p[2])<.75
                    and abs(position[1]-p[1])<.35 for _,p in visits)
        length=sum(math.hypot(a[0]-b[0],a[2]-b[2]) for (_,a),(_,b) in zip(visits,visits[1:]))
        return returns>=4 and length>=6.
