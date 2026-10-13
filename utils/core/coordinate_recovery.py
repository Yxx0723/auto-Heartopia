"""Bounded detours and progress-based failure accounting; no device access."""
from dataclasses import dataclass

from utils.core import thresholds as policy
from utils.core.coordinate_route import distance, valid_position


@dataclass
class RecoveryProgress:
    origin: tuple | None = None
    failures: int = 0
    attempts: int = 0
    pending: bool = False

    def begin(self, position):
        if self.origin is None:
            self.origin = tuple(position)
        self.attempts += 1
        # Keep interrupted and provisionally successful actions pending until
        # real forward progress proves clearance, or another stall charges it.
        self.pending = True

    def fail(self):
        if not self.pending:
            return False
        self.failures += 1
        self.pending = False
        return True

    def observe(self, position, target):
        if self.origin is None:
            return False
        remaining = distance(position, target)
        if remaining <= policy.COORD_REACH_RADIUS or distance(self.origin, target) - remaining \
                >= policy.COORD_RECOVERY_CONFIRM_DISTANCE:
            self.reset()
            return True
        return False

    def reset(self):
        self.origin, self.failures, self.attempts, self.pending = None, 0, 0, False


def validate_recovery_options(attempts, jump):
    if type(attempts) is not int or not 0 <= attempts <= policy.COORD_RECOVERY_MAX_ATTEMPTS:
        raise ValueError(f'脱困次数应为 0～{policy.COORD_RECOVERY_MAX_ATTEMPTS} 的整数。')
    if type(jump) is not bool:
        raise ValueError('跳跃脱困开关无效。')


def recovery_plan(origin, target, attempt):
    length = distance(origin, target)
    if not valid_position(origin) or not valid_position(target) or length <= policy.COORD_REACH_RADIUS:
        raise ValueError('无法确定脱困方向。')
    dx, dz = (target[0] - origin[0]) / length, (target[2] - origin[2]) / length
    side = 1 if attempt % 2 else -1
    back = [origin[0] - dx * policy.COORD_RECOVERY_BACK_DISTANCE, origin[1],
            origin[2] - dz * policy.COORD_RECOVERY_BACK_DISTANCE]
    width = min(policy.COORD_RECOVERY_MAX_SIDE_DISTANCE, policy.COORD_RECOVERY_SIDE_DISTANCE
                + ((attempt - 1) // 2) * policy.COORD_RECOVERY_SIDE_STEP)
    lateral = [back[0] - dz * side * width, back[1], back[2] + dx * side * width]
    ahead = min(policy.COORD_RECOVERY_FORWARD_DISTANCE, length + policy.COORD_RECOVERY_BACK_DISTANCE)
    forward = [lateral[0] + dx * ahead, lateral[1], lateral[2] + dz * ahead]
    return [back, lateral, forward]


def recovery_within_limits(position, origin, segment_start, target):
    if not all(valid_position(p) for p in (position, origin, segment_start, target)):
        return False
    dx, dz = target[0] - segment_start[0], target[2] - segment_start[2]
    length2 = dx * dx + dz * dz
    fraction = max(0., min(1., ((position[0] - segment_start[0]) * dx
                               + (position[2] - segment_start[2]) * dz) / length2)) if length2 else 0.
    nearest = [segment_start[0] + fraction * dx, position[1], segment_start[2] + fraction * dz]
    return (distance(position, origin) <= policy.COORD_RECOVERY_MAX_RADIUS
            and distance(position, nearest) <= policy.COORD_RECOVERY_MAX_RADIUS
            and origin[1] - position[1] <= policy.COORD_MAX_HEIGHT_ERROR)
