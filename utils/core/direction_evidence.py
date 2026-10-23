"""Bounded observation quality and calibration budgets, not camera telemetry."""
import math
from utils.core import thresholds as policy


class DirectionEvidence:
    def __init__(self):
        self.samples, self.scope = [], None

    def observe(self, expected, actual, *, now, geometry_clear=True, height_delta=0., mode='walk', scope=None):
        if any(not isinstance(v,(list,tuple)) or len(v) != 2 for v in (expected,actual)) or not all(
                type(v) in (int,float) and math.isfinite(v) for v in (*expected,*actual,now,height_delta)):
            self.samples = []
            return {'state':'unknown', 'camera_observed':False, 'reason':'invalid_observation'}
        if scope != self.scope:
            self.samples, self.scope = [], scope
        self.samples = [sample for sample in self.samples if 0 <= now-sample[0] <= policy.COORD_DIRECTION_EVIDENCE_SECONDS]
        result = {'state': 'unknown', 'camera_observed': False, 'reason': 'insufficient_observations'}
        if mode != 'walk' or abs(height_delta) > policy.COORD_DIRECTION_SAMPLE_HEIGHT:
            self.samples = []
            return {**result, 'reason': 'unstable_height_or_mode'}
        lengths = math.hypot(*expected), math.hypot(*actual)
        if min(lengths) < policy.COORD_MIN_CALIBRATION:
            return {**result, 'reason': 'insufficient_displacement'}
        if not geometry_clear:
            self.samples = []
            return {**result, 'state': 'obstacle_suspected', 'reason': 'known_geometry_conflict'}
        angle = math.atan2(actual[1], actual[0])-math.atan2(expected[1], expected[0])
        angle = math.degrees(math.atan2(math.sin(angle), math.cos(angle)))
        if abs(angle) <= policy.COORD_DIRECTION_ALIGNED_DEGREES:
            self.samples = []
            return {**result, 'state': 'aligned', 'reason': 'consistent_motion', 'angle_degrees': angle}
        self.samples = [sample for sample in self.samples if abs(
            (angle-sample[2]+180)%360-180) <= policy.COORD_DIRECTION_AGREEMENT_DEGREES]
        axis = tuple(v/lengths[0] for v in expected)
        self.samples.append((now, axis, angle))
        self.samples = self.samples[-policy.COORD_DIRECTION_EVIDENCE_LIMIT:]
        independent = any(abs(sum(a*b for a,b in zip(axis, sample[1]))) < policy.COORD_DIRECTION_INDEPENDENT_COSINE
                          for sample in self.samples)
        if len(self.samples) >= policy.COORD_DIRECTION_CONFIRM_SAMPLES and independent:
            return {**result, 'state': 'rotation_candidate', 'reason': 'consistent_independent_axes', 'angle_degrees': angle}
        return {**result, 'angle_degrees': angle}


class CalibrationBudget:
    def __init__(self):
        self.tag, self.scope, self.total, self.regions = None, None, 0, []

    def begin(self, position, waypoint, scope):
        if waypoint != self.tag:
            self.tag, self.total, self.regions = waypoint, 0, []
        if scope != self.scope:
            self.scope, self.regions = scope, []
        region = next((r for r in self.regions if math.dist(position, r[0]) <= policy.COORD_CALIBRATION_REGION_RADIUS), None)
        if self.total >= policy.COORD_CALIBRATION_POINT_ATTEMPTS or (
                region is not None and region[1] >= policy.COORD_CALIBRATION_REGION_ATTEMPTS):
            return False
        if region is None:
            region = [tuple(position), 0]
            self.regions.append(region)
        region[1] += 1
        self.total += 1
        return True
