"""Narrow, surveyed walking links; water stays blocked outside their corridor.

These are explicit crossings, separate from disabled learned route memories.
Analytic interval coverage avoids stepping across a gap between sampled points.
No bridge name, display mesh or endpoint alone establishes a walking link.
"""
import math

from utils.core import thresholds as policy
from utils.core.coordinate_route import valid_position


def bridge_signature(scene):
    return (scene.get('world_state'), scene.get('bridge_revision'),
            tuple((bridge.get('source'), bool(bridge.get('map_cached')),
                   tuple(tuple(p) for p in bridge.get('points', ()) if valid_position(p)))
                  for bridge in scene.get('bridge_crossings', ())))


def merge_intervals(intervals):
    merged = []
    for low, high in sorted((max(0., a), min(1., b)) for a, b in intervals if a <= 1. and b >= 0.):
        if high < low:
            continue
        if merged and low <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return merged


def linear_interval(value, delta, low, high):
    if abs(delta) < 1e-12:
        return (0., 1.) if low <= value <= high else None
    ends = sorted(((low-value)/delta, (high-value)/delta))
    a, b = max(0., ends[0]), min(1., ends[1])
    return (a, b) if a <= b else None


def capsule_intervals(a, b, c, d, radius):
    """Parameter intervals of AB inside the closed radius tube around CD."""
    velocity = (b[0]-a[0], b[1]-a[1])
    speed2 = sum(v*v for v in velocity)
    intervals = []
    for center in (c, d):
        offset = (a[0]-center[0], a[1]-center[1])
        dot = sum(v*w for v, w in zip(offset, velocity))
        constant = sum(v*v for v in offset)-radius*radius
        if speed2 < 1e-20:
            if constant <= 1e-12:
                intervals.append((0., 1.))
        else:
            discriminant = dot*dot-speed2*constant
            if discriminant >= -1e-12:
                root = math.sqrt(max(0., discriminant))
                intervals.append(((-dot-root)/speed2, (-dot+root)/speed2))
    length = math.dist(c, d)
    if length > 1e-12:
        axis = ((d[0]-c[0])/length, (d[1]-c[1])/length)
        offset = (a[0]-c[0], a[1]-c[1])
        along = linear_interval(sum(x*y for x, y in zip(offset, axis)),
                                sum(x*y for x, y in zip(velocity, axis)), 0., length)
        side = linear_interval(offset[0]*axis[1]-offset[1]*axis[0],
                               velocity[0]*axis[1]-velocity[1]*axis[0], -radius, radius)
        if along and side:
            intervals.append((max(along[0], side[0]), min(along[1], side[1])))
    return merge_intervals((low, high) for low, high in intervals if low <= high)


def polygon_intervals(a, b, polygon, clearance):
    """Exact line/polygon interior plus edge capsules for the water margin."""
    delta = (b[0]-a[0], b[1]-a[1])
    cuts, intervals = [0., 1.], []
    for c, d in zip(polygon, polygon[1:]+polygon[:1]):
        intervals.extend(capsule_intervals(a, b, c, d, clearance))
        edge = (d[0]-c[0], d[1]-c[1])
        denominator = delta[0]*edge[1]-delta[1]*edge[0]
        if abs(denominator) > 1e-12:
            offset = (c[0]-a[0], c[1]-a[1])
            t = (offset[0]*edge[1]-offset[1]*edge[0])/denominator
            u = (offset[0]*delta[1]-offset[1]*delta[0])/denominator
            if 0. <= t <= 1. and 0. <= u <= 1.:
                cuts.append(t)
    def inside(point):
        x, z = point
        result = False
        for c, d in zip(polygon, polygon[1:]+polygon[:1]):
            if (c[1] > z) != (d[1] > z) and x < (d[0]-c[0])*(z-c[1])/(d[1]-c[1])+c[0]:
                result = not result
        return result
    cuts.sort()
    for low, high in zip(cuts, cuts[1:]):
        middle = (low+high)/2
        if inside((a[0]+delta[0]*middle, a[1]+delta[1]*middle)):
            intervals.append((low, high))
    return merge_intervals(intervals)


class BridgeSupport:
    def __init__(self, scene, position):
        self.paths = []
        if scene.get('world_state') != 'ready' or not scene.get('bridge_revision'):
            return
        for crossing in scene.get('bridge_crossings', ()):
            points = crossing.get('points', ())
            if crossing.get('source') != 'surveyed-walking-bridge' or crossing.get('map_cached') \
                    or not 2 <= len(points) <= policy.COORD_BRIDGE_MAX_POINTS \
                    or not all(valid_position(p) for p in points) \
                    or any(math.dist(a, b) > policy.COORD_BRIDGE_MAX_SAMPLE_GAP for a, b in zip(points, points[1:])):
                continue
            # The XZ planner cannot distinguish stacked floors. Limit this
            # link to its surveyed height band, never a swimmer below a bridge.
            if max(abs(p[1]-position[1]) for p in points) > policy.COORD_BRIDGE_HEIGHT_TOLERANCE:
                continue
            self.paths.append(tuple(tuple(p) for p in points))

    def permits(self, a, b, polygon, clearance):
        if not self.paths:
            return False
        radius = policy.COORD_BRIDGE_CORRIDOR_RADIUS
        covered = merge_intervals(interval for path in self.paths for c, d in zip(path, path[1:])
                                  if min(a[0], b[0])-radius <= max(c[0], d[0])
                                  and max(a[0], b[0])+radius >= min(c[0], d[0])
                                  and min(a[1], b[1])-radius <= max(c[2], d[2])
                                  and max(a[1], b[1])+radius >= min(c[2], d[2])
                                  for interval in capsule_intervals(a, b, (c[0], c[2]), (d[0], d[2]),
                                                                    policy.COORD_BRIDGE_CORRIDOR_RADIUS))
        if not covered:
            return False
        return all(any(start <= low+1e-9 and end >= high-1e-9 for start, end in covered)
                   for low, high in polygon_intervals(a, b, polygon, clearance))
