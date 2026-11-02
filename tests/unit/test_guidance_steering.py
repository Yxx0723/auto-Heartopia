"""Offline steering regressions; no game input or claim of natural in-game motion."""
import math

import pytest

from tests.unit.test_coordinate_navigation import box, scene
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap
from utils.core.guidance_navigation import GuidanceNavigator


BASIS = {'forward': [0., 1.], 'right': [1., 0.]}


def target(degrees, length=20.):
    radians = math.radians(degrees)
    return (math.sin(radians)*length, 0., math.cos(radians)*length)


def navigator():
    now = [0.]
    nav = GuidanceNavigator(lambda: scene(), clock=lambda: now[0], water_areas=())
    nav.obstacles = ObstacleMap(scene(), (0, 0, 0), water_areas=())
    return nav, now


@pytest.mark.parametrize('first,expected', [(22.4, {'w'}), (22.6, {'w', 'd'})])
def test_small_boundary_noise_does_not_alternate_keys(first, expected):
    nav, now = navigator()
    assert nav.steering_keys((0, 0, 0), target(first), BASIS, .3) == expected
    for index, angle in enumerate([22.4, 22.6]*10):
        now[0] = (index+1)*.05
        assert nav.steering_keys((0, 0, 0), target(angle), BASIS, .3) == expected


def test_camera_basis_noise_at_sector_boundary_does_not_alternate_keys():
    nav, now = navigator()
    for index, angle in enumerate([.2, -.2]*10):
        radians = math.radians(angle)
        basis = {'forward': [math.sin(radians), math.cos(radians)],
                 'right': [math.cos(radians), -math.sin(radians)]}
        now[0] = index*.05
        assert nav.steering_keys((0, 0, 0), target(22.5), basis, .3) == {'w'}


@pytest.mark.parametrize('angle,expected', [(35., {'w', 'd'}), (90., {'d'}), (180., {'s'})])
def test_real_turn_is_not_delayed(angle, expected):
    nav, now = navigator()
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3)
    now[0] = .05
    assert nav.steering_keys((0, 0, 0), target(angle), BASIS, .3) == expected


@pytest.mark.parametrize('water', [False, True])
def test_retained_direction_is_rechecked_against_new_wall_or_water(water):
    nav, now = navigator()
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, 2.)
    obstacle = box(-.05, .05, 1.8, 1.9)
    nav.obstacles = ObstacleMap(scene() if water else scene(obstacle), (0, 0, 0),
                               water_areas=[obstacle['polygon']] if water else ())
    now[0] = .05
    chosen = nav.steering_keys((0, 0, 0), target(22.6), BASIS, 2.)
    assert chosen != {'w'}
    from utils.core.coordinate_route import key_vector
    vector = key_vector(chosen, BASIS)
    norm = math.hypot(*vector)
    assert nav.obstacles.clear((0, 0), tuple(v/norm*2. for v in vector), escape=True)


def test_arrival_cannot_be_overridden_by_retained_direction():
    nav, now = navigator()
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3)
    now[0] = .05
    assert nav.steering_keys((0, 0, 0), (0, 0, 0), BASIS, .3) == set()


@pytest.mark.parametrize('reset', ['invalidate', 'expired', 'height', 'scene'])
def test_old_steering_state_is_not_reused_across_discontinuity(reset):
    nav, now = navigator()
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3)
    now[0] = .05
    position = (0, 0, 0)
    if reset == 'invalidate':
        nav.invalidate()
        nav.obstacles = ObstacleMap(scene(), position, water_areas=())
    elif reset == 'expired':
        now[0] = 1.
    elif reset == 'height':
        position = (0, 2., 0)
    else:
        nav.scene = {'cache_context': 'different-world'}
    assert nav.steering_keys(position, target(22.6), BASIS, .3) == {'w', 'd'}


def test_ordinary_playback_retains_existing_stateless_selection():
    nav = LocalNavigator(lambda: scene(), water_areas=())
    nav.obstacles = ObstacleMap(scene(), (0, 0, 0), water_areas=())
    assert nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3) == {'w'}
    assert nav.steering_keys((0, 0, 0), target(22.6), BASIS, .3) == {'w', 'd'}


@pytest.mark.parametrize('sector', range(8))
def test_all_eight_boundaries_have_bounded_hysteresis(sector):
    nav, now = navigator()
    center = sector*45.+22.5
    first = nav.steering_keys((0, 0, 0), target(center-.1), BASIS, .3)
    for i in range(12):
        now[0] += .05
        assert nav.steering_keys((0, 0, 0), target(center+(-1)**i*.1), BASIS, .3) == first
    now[0] += .05
    assert nav.steering_keys((0, 0, 0), target(center+8.), BASIS, .3) != first
    assert nav.steering_stats['holds'] > 0


def test_large_camera_turn_discards_old_key_sector():
    nav, now = navigator()
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3)
    radians = math.radians(-10.)
    basis = {'forward': [math.sin(radians), math.cos(radians)],
             'right': [math.cos(radians), -math.sin(radians)]}
    now[0] = .05
    assert nav.steering_keys((0, 0, 0), target(22.6), basis, .3) == {'w', 'd'}


def test_position_discontinuity_cannot_inherit_old_sector():
    nav, now = navigator()
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3)
    now[0] = .05
    far = (20., 0., 0.)
    aim = target(22.6)
    assert nav.steering_keys(far, (aim[0]+20., 0., aim[2]), BASIS, .3) == {'w', 'd'}


def test_new_failed_entry_risk_cannot_be_overridden_by_hysteresis():
    nav, now = navigator()
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3)
    for attempt in (1, 2):
        nav.risks.record((0, 0, 0), target(22.4), attempt_id=attempt)
    now[0] = .05
    chosen = nav.steering_keys((0, 0, 0), target(22.6), BASIS, .3)
    from utils.core.coordinate_route import key_vector
    vector = key_vector(chosen, BASIS)
    norm = math.hypot(*vector)
    end = tuple(v/norm*1.6 for v in vector)
    assert not nav.risks.repeats_failed_entry((0, 0), end, 0.)


def test_explicit_recovery_map_does_not_use_walk_sector_hold():
    nav, now = navigator()
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3)
    now[0] = .05
    assert nav.steering_keys((0, 0, 0), target(22.6), BASIS, .3,
                             obstacles=nav.obstacles) == {'w', 'd'}
    assert nav._steering_state is None


def test_controller_release_resets_sector_and_reports_bounded_counters():
    from tests.unit.test_guidance_cycle import follower
    from tests.unit.test_native_guidance_follow import guide_game
    game = guide_game()
    f = follower(game)
    nav = f.navigator
    nav.obstacles = ObstacleMap(scene(), (0, 0, 0), water_areas=())
    nav.steering_keys((0, 0, 0), target(22.4), BASIS, .3)
    f.release('invalid_direction')
    assert nav._steering_state is None
    assert nav.steering_keys((0, 0, 0), target(22.6), BASIS, .3) == {'w', 'd'}
    f.report('playing', 'test')
    assert f._report.call_args.kwargs['steering_metrics'] == nav.steering_stats
    f.input_lease.close()
