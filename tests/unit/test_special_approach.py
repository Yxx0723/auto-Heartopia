"""Standpoint choice and last-metre stall detection beside dynamic resources."""
from utils.core import thresholds as policy
from utils.core.coordinate_navigation import ObstacleMap
from utils.core.special_approach import ApproachProgress, interaction_standpoint, in_interaction_range


def wall(x, z, half=.15):
    return {'polygon': [[x-half, z-half], [x+half, z-half], [x+half, z+half], [x-half, z+half]],
            'height': [0., 2.]}


def map_with(*colliders):
    return ObstacleMap({'colliders': list(colliders)}, [0., 0., 0.], water_areas=())


def test_a_blocked_standpoint_is_replaced_and_never_repeated():
    obstacles = map_with()
    first = interaction_standpoint([3., 0., 0.], [0., 0., 0.], obstacles)
    second = interaction_standpoint([3., 0., 0.], [0., 0., 0.], obstacles, avoid=[first])
    assert second != first
    assert in_interaction_range(second, [0., 0., 0.])


def test_every_ring_point_can_be_rejected_without_losing_the_approach():
    obstacles = map_with()
    avoid = [interaction_standpoint([3., 0., 0.], [0., 0., 0.], obstacles)]
    for _ in range(40):
        avoid.append(interaction_standpoint([3., 0., 0.], [0., 0., 0.], obstacles, avoid=avoid))
    assert in_interaction_range(avoid[-1], [0., 0., 0.])


def test_a_slow_creep_inside_the_envelope_is_reported_as_a_stall():
    progress = ApproachProgress()
    assert not progress.stalled([1.30, 0., 0.], [0., 0., 0.], 0.)
    assert not progress.stalled([1.29, 0., 0.], [0., 0., 0.], .5)
    assert progress.stalled([1.28, 0., 0.], [0., 0., 0.], policy.COORD_SPECIAL_APPROACH_STALL_SECONDS)


def test_real_progress_resets_the_stall_window():
    progress = ApproachProgress()
    assert not progress.stalled([1.9, 0., 0.], [0., 0., 0.], 0.)
    assert not progress.stalled([1.4, 0., 0.], [0., 0., 0.], 1.)      # walked a full unit
    assert not progress.stalled([1.39, 0., 0.], [0., 0., 0.], 2.)     # 1.0 s since progress
    assert progress.stalled([1.38, 0., 0.], [0., 0., 0.], 2.5)        # 1.5 s since progress


def test_far_away_motion_is_not_treated_as_a_last_metre_stall():
    progress = ApproachProgress()
    far = policy.COORD_SPECIAL_APPROACH_WATCH_RADIUS + 5
    assert not progress.stalled([far, 0., 0.], [0., 0., 0.], 0.)
    assert not progress.stalled([far, 0., 0.], [0., 0., 0.], 30.)
