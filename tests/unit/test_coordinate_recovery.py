"""Recovery geometry is data only, with explicit local movement limits."""
import math

import pytest

from utils.core.coordinate_recovery import (recovery_plan, recovery_within_limits,
                                             validate_recovery_options)


@pytest.mark.parametrize('attempts', [0, 1, 3, 10])
def test_allowed_counts_and_optional_jump(attempts):
    validate_recovery_options(attempts, False)
    validate_recovery_options(attempts, True)


@pytest.mark.parametrize('attempts,jump', [(-1, False), (11, False), (True, False),
                                         (1.5, False), ('3', False), (3, 1)])
def test_invalid_options_are_rejected(attempts, jump):
    with pytest.raises(ValueError):
        validate_recovery_options(attempts, jump)


def test_attempts_alternate_around_the_same_obstacle_anchor():
    origin, target = [0, 2, 2], [0, 2, 8]
    left = recovery_plan(origin, target, 1)
    right = recovery_plan(origin, target, 2)
    assert left[0][2] < origin[2] and left[0] == right[0]
    assert left[1][0] < 0 < right[1][0]
    assert left[2][2] > origin[2] and right[2][2] > origin[2]
    assert all(p[1] == 2 for p in left + right)
    wider = recovery_plan(origin, target, 3)
    assert wider[0] == left[0] and wider[1][0] < left[1][0]
    assert wider[2][2] == left[2][2]


def test_expanding_detours_remain_inside_the_original_local_envelope():
    origin, target = [0, 0, 2], [0, 0, 8]
    widths = []
    for attempt in range(1, 11):
        plan = recovery_plan(origin, target, attempt)
        widths.append(abs(plan[1][0]))
        assert all(recovery_within_limits(p, origin, [0, 0, 0], target) for p in plan)
    assert widths == pytest.approx([1.2, 1.2, 1.8, 1.8, 2.4, 2.4, 2.4, 2.4, 2.4, 2.4])


def test_pending_or_interrupted_attempt_is_charged_once_and_only_real_progress_resets_it():
    from utils.core.coordinate_recovery import RecoveryProgress
    recovery = RecoveryProgress()
    recovery.begin([0, 0, 2])
    assert not recovery.observe([0, 5, 2.2], [0, 0, 10])
    assert recovery.fail() and recovery.failures == 1
    assert not recovery.fail() and recovery.failures == 1
    recovery.begin([0, 0, 2.2])
    assert recovery.origin == (0, 0, 2) and recovery.attempts == 2
    assert not recovery.observe([1, 0, 2], [0, 0, 10])
    assert recovery.observe([0, 0, 3.1], [0, 0, 10])
    assert recovery.failures == recovery.attempts == 0 and recovery.origin is None
    assert not recovery.fail()


def test_reaching_a_nearby_target_clears_a_pending_attempt_without_requiring_a_full_unit():
    from utils.core.coordinate_recovery import RecoveryProgress
    recovery = RecoveryProgress()
    recovery.begin([0, 0, 2])
    assert recovery.observe([0, 0, 2.5], [0, 0, 2.6])
    assert recovery.failures == 0 and not recovery.pending


def test_plan_uses_world_target_direction_instead_of_assuming_w_is_forward():
    plan = recovery_plan([2, 0, 0], [8, 0, 0], 1)
    assert plan[0][0] < 2 and plan[1][2] > 0 and plan[2][0] > 2
    with pytest.raises(ValueError):
        recovery_plan([0, 0, 0], [0, 0, 0], 1)


def test_local_radius_route_corridor_and_height_drop_are_all_bounded():
    origin, start, target = [0, 0, 3], [0, 0, 0], [0, 0, 10]
    assert recovery_within_limits([1, 0, 4], origin, start, target)
    assert not recovery_within_limits([0, 0, 8], origin, start, target)
    assert not recovery_within_limits([5, 0, 4], [5, 0, 3], start, target)
    assert not recovery_within_limits([0, -2, 3], origin, start, target)
    assert not recovery_within_limits([math.nan, 0, 3], origin, start, target)
