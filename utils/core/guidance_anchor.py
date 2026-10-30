"""Task-local planning anchors, not saved waypoints or an arrival policy."""
import bisect
import math

from utils.core import thresholds as policy
from utils.core.coordinate_navigation import point_segment_distance


class GuidanceAnchor:
    def __init__(self):
        self.reset()

    def reset(self):
        self.revision, self.point = None, None
        self.arc, self.fallback_until = 0., 0.
        self.changes = 0

    def reject(self, cursor):
        self.point = None
        self.fallback_until = cursor.arc+policy.COORD_GUIDANCE_ANCHOR_LOOKAHEAD

    def choose(self, cursor, position, aim):
        if self.revision != cursor.revision:
            self.reset()
            self.revision = cursor.revision
        if aim['arrived'] or cursor.arc < self.fallback_until:
            return aim['position']
        if self.point is not None:
            if cursor.arc < self.arc-policy.COORD_NAV_LEG_REACH_RADIUS \
                    and math.hypot(position[0]-self.point[0], position[2]-self.point[2]) > policy.COORD_NAV_LEG_REACH_RADIUS \
                    and abs(position[1]-self.point[1]) < policy.COORD_NAV_HEIGHT_REFRESH:
                return self.point
            self.point = None
        candidate = cursor.aim(position, lookahead=policy.COORD_GUIDANCE_ANCHOR_LOOKAHEAD)
        end_arc = min(cursor.lengths[-1], cursor.arc+policy.COORD_GUIDANCE_ANCHOR_LOOKAHEAD)
        a, b = (position[0], position[2]), (candidate['position'][0], candidate['position'][2])
        begin = bisect.bisect_right(cursor.lengths, cursor.arc)
        end = bisect.bisect_left(cursor.lengths, end_arc)
        if abs(candidate['height_hint']-position[1]) > policy.COORD_GUIDANCE_HEIGHT_HINT_BAND \
                or any(point_segment_distance((p[0], p[2]), a, b) > policy.COORD_GUIDANCE_ANCHOR_CHORD_ERROR
                       for p in cursor.path[begin:end]):
            return aim['position']  # Keep the existing short lookahead at bends.
        self.point, self.arc = candidate['position'], end_arc
        self.changes += 1
        return self.point
