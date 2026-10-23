"""Ephemeral directed approach costs. Never creates a physical wall/support ID."""
import math
import time
from utils.core import thresholds as policy
from utils.core.coordinate_route import valid_position


def segment_gap(point, start, end):
    vector = tuple(b-a for a,b in zip(start,end))
    length2 = sum(v*v for v in vector)
    part = max(0., min(1., sum((p-a)*v for p,a,v in zip(point,start,vector))/length2)) if length2 else 0.
    return math.dist(point, tuple(a+part*v for a,v in zip(start,vector)))


def local_geometry(obstacles, origin):
    if obstacles is None or not obstacles.covers_search(origin, policy.COORD_APPROACH_RISK_RADIUS):
        return None
    radius = policy.COORD_APPROACH_RISK_RADIUS
    indexes = set()
    for x in range(math.floor((origin[0]-radius)/4), math.floor((origin[0]+radius)/4)+1):
        for z in range(math.floor((origin[2]-radius)/4), math.floor((origin[2]+radius)/4)+1):
            indexes.update(obstacles.bins.get((x,z), ()))
    return frozenset(obstacles.polygons[i] for i in indexes)


class ApproachRisks:
    def __init__(self, clock=time.monotonic):
        self.clock, self.entries, self.scope, self.revision = clock, [], None, 0

    @property
    def count(self):
        return len(self.entries)

    def clear(self):
        if self.entries:
            self.entries = []
            self.revision += 1

    def bind(self, scope):
        if scope != self.scope:
            self.clear()
            self.scope = scope

    def expire(self, now):
        entries = [e for e in self.entries if e['expires_at'] > now]
        if len(entries) != len(self.entries):
            self.entries = entries
            self.revision += 1

    def record(self, start, end, *, attempt_id=None, mode='walk', geometry=None):
        if not valid_position(start) or not valid_position(end):
            return
        dx, dz = end[0]-start[0], end[2]-start[2]
        length = math.hypot(dx,dz)
        if length < 1e-6:
            return
        identity = (attempt_id, mode) if attempt_id is not None else (tuple(round(v,3) for v in start),
                    round(dx/length,2), round(dz/length,2), mode)
        if any(e['identity'] == identity for e in self.entries):
            return
        direction = dx/length,dz/length
        self.entries.append({'identity':identity, 'start':tuple(start), 'direction':direction,
                             'focus':(start[0]+direction[0]*min(policy.COORD_APPROACH_FOCUS_DISTANCE,length),
                                      start[2]+direction[1]*min(policy.COORD_APPROACH_FOCUS_DISTANCE,length)),
                             'mode':mode, 'source':'movement_stall', 'support_ref':None,
                             'confidence':'temporary_risk', 'geometry':geometry,
                             'expires_at':self.clock()+policy.COORD_NAV_PROBE_SECONDS})
        self.entries = self.entries[-policy.COORD_APPROACH_RISK_LIMIT:]
        self.revision += 1

    def cost(self, start, end, height, mode='walk'):
        length = math.dist(start,end)
        if length < 1e-9:
            return 0.
        direction = tuple((b-a)/length for a,b in zip(start,end))
        result = 0.
        for entry in self.entries:
            if entry['mode'] != mode or abs(height-entry['start'][1]) > policy.COORD_APPROACH_HEIGHT_BAND:
                continue
            alignment = sum(a*b for a,b in zip(direction,entry['direction']))
            if alignment < policy.COORD_APPROACH_DIRECTION_COSINE:
                continue
            gap = segment_gap(entry['focus'],start,end)
            if gap < policy.COORD_APPROACH_RISK_RADIUS:
                result += policy.COORD_APPROACH_RISK_WEIGHT*(1-gap/policy.COORD_APPROACH_RISK_RADIUS)*min(
                    length, 2*policy.COORD_APPROACH_RISK_RADIUS)
        return result

    def success(self, start, end, mode='walk'):
        if not valid_position(start) or not valid_position(end) or abs(start[1]-end[1]) > policy.COORD_DIRECTION_SAMPLE_HEIGHT:
            return
        length = math.hypot(end[0]-start[0],end[2]-start[2])
        if length < policy.COORD_MIN_CALIBRATION:
            return
        direction = ((end[0]-start[0])/length,(end[2]-start[2])/length)
        entries = []
        for entry in self.entries:
            passed = sum((end[i]-entry['start'][i])*entry['direction'][j] for j,i in enumerate((0,2)))
            clears = entry['mode'] == mode and abs(start[1]-entry['start'][1]) <= policy.COORD_APPROACH_HEIGHT_BAND \
                and sum(a*b for a,b in zip(direction,entry['direction'])) >= policy.COORD_APPROACH_SUCCESS_COSINE \
                and passed >= policy.COORD_APPROACH_RISK_RADIUS+policy.COORD_APPROACH_FOCUS_DISTANCE \
                and segment_gap(entry['focus'],(start[0],start[2]),(end[0],end[2])) < policy.COORD_APPROACH_SUCCESS_LATERAL
            if not clears:
                entries.append(entry)
        if len(entries) != len(self.entries):
            self.entries = entries
            self.revision += 1

    def revalidate(self, obstacles):
        entries = [e for e in self.entries if e['geometry'] is None or local_geometry(obstacles,e['start']) in (
            None,e['geometry'])]
        if len(entries) != len(self.entries):
            self.entries = entries
            self.revision += 1
