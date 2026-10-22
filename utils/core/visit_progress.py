"""Finite point attempts. Movement to new cells counts even on a long detour."""
import math
from utils.core import thresholds as policy
from utils.core.coordinate_route import distance


class MotionProgress:
    """Actual XZ progress independent of changes to a temporary steering leg.

    Supply active time; reset after calibration, recovery or controlled stops.
    A new planning leg must not erase centimetre-scale oscillation evidence.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.anchor, self.since = None, None

    def stalled(self, position, active_time):
        if self.anchor is None or self.since is None or active_time < self.since \
                or distance(position, self.anchor) >= policy.COORD_STUCK_DISTANCE:
            self.anchor, self.since = tuple(position), active_time
            return False
        return active_time-self.since >= policy.COORD_STUCK_SECONDS


class VisitProgress:
    def __init__(self, position, target, now):
        self.started = self.last = now
        self.elapsed = self.stalled = 0.
        self.seen = set()
        self.limit = min(policy.COORD_VISIT_MAX_SECONDS,
            max(policy.COORD_VISIT_MIN_SECONDS, distance(position, target) * policy.COORD_VISIT_SECONDS_PER_UNIT))

    def pause(self, now):
        self.last = now

    def observe(self, position, now, ready=True):
        dt, self.last = max(0., now-self.last), now
        if not ready:
            return None
        self.elapsed += dt
        cell = tuple(math.floor(position[i] / policy.COORD_VISIT_CELL_SIZE) for i in (0, 2))
        if cell not in self.seen:
            self.seen.add(cell)
            self.stalled = 0.
        else:
            self.stalled += dt
        if self.stalled >= policy.COORD_VISIT_STALL_SECONDS:
            return '长时间未找到新的通行位置'
        if self.elapsed >= self.limit:
            return '本次到达尝试超过时间预算'
        return None
