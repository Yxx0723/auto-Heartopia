"""Recorded bridge-side padding and synthetic constraints; no real game input."""
import math

import pytest

from tests.unit.test_coordinate_navigation import box, scene
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait, polygon_distance
from utils.core.guidance_navigation import GuidanceNavigator


START = (243.26483154296875, 12.390625, 87.56023406982422)
AIM = (244.2436228774233, START[1], 88.26557069617613)
RAIL = [[242.26345345587993, 85.61020119853208],
        [246.38845345587993, 89.36020119853208],
        [246.36154654412007, 89.38979880146792],
        [242.23654654412007, 85.63979880146792]]


def source(*others):
    # Category is unconfirmed in the real run; using non-approachable geometry
    # reproduces its full .6 clearance, without claiming this is a real rail.
    return scene({'polygon': RAIL, 'height': [11., 15.], 'category': 'mountain'}, *others)


def navigator(value=None):
    return GuidanceNavigator(lambda: source() if value is None else value,
                             respect_water=False, water_areas=())


def test_recorded_guide_point_in_padding_gets_small_fully_checked_alternative():
    nav = navigator()
    selected = nav.next_target(START, AIM)
    assert selected != AIM and .01 < math.dist(selected, AIM) < .03
    assert selected[1] == AIM[1]
    a, b = (START[0], START[2]), (selected[0], selected[2])
    assert nav.obstacles.clear(a, b)
    assert polygon_distance(b, b, RAIL) >= .6
    assert nav.last_decision['kind'] == 'guidance_padding_alternative'
    assert nav.last_decision['requested_target'] == AIM


def test_ordinary_route_and_legacy_water_policy_keep_existing_block():
    for nav in [LocalNavigator(source, water_areas=()),
                GuidanceNavigator(source, water_areas=())]:
        with pytest.raises(NavigationWait):
            nav.next_target(START, AIM)


def test_point_inside_actual_body_cannot_be_projected_out():
    nav = navigator()
    center = tuple(sum(p[i] for p in RAIL)/4 for i in (0, 1))
    with pytest.raises(NavigationWait):
        nav.next_target(START, (center[0], AIM[1], center[1]))


def test_start_in_padding_does_not_grant_a_new_free_connection():
    nav = navigator()
    with pytest.raises(NavigationWait):
        nav.next_target(AIM, AIM)


def test_new_wall_at_alternative_cannot_be_bypassed():
    wall = box(244.15, 244.3, 88.2, 88.35, (11., 15.))
    nav = navigator(source(wall))
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_no_risk_free_candidate_keeps_waiting():
    nav = navigator()
    nav._refresh_obstacles(START, AIM)
    nav.obstacles.risk_cost = lambda a, b: 1.
    assert nav._shore_target(START, AIM, lambda: None) is None


def test_projected_aim_never_changes_cursor_path_destination_or_arrival():
    from utils.core.guidance_path import GuidanceCursor, validate_guidance
    from tests.unit.test_native_guidance_follow import snapshot
    cursor = GuidanceCursor(validate_guidance(snapshot(path=(START, AIM), target=AIM), 0.), START)
    before = cursor.path, cursor.target, cursor.revision
    nav = navigator()
    nav.next_target(START, cursor.aim(START)['position'])
    assert (cursor.path, cursor.target, cursor.revision) == before
    assert not cursor.aim(START)['arrived']


def test_synthetic_closed_loop_passes_recorded_local_aim_without_interaction():
    from tests.unit.test_native_guidance_follow import guide_game, run, snapshot
    game = guide_game()
    game.position = list(START)
    game.guidance = lambda: snapshot(path=(START, AIM), target=AIM, now=game.time)
    nav = GuidanceNavigator(source, clock=game.monotonic, water_areas=(), respect_water=False)
    result = run(game, navigator=nav)
    assert result['arrived'] and math.dist(game.position, AIM) <= .8
    assert not game.keys and not any(e['key'] in ('f', 'b', 't') for _, e in game.events)


def test_adjustment_cap_rejects_large_or_disabled_nudges(monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_GUIDANCE_PADDING_MAX_OFFSET', .005)
    with pytest.raises(NavigationWait):
        navigator().next_target(START, AIM)


def test_refresh_revalidates_old_alternative_when_a_new_wall_appears():
    current = [source()]
    nav = GuidanceNavigator(lambda: current[0], respect_water=False, water_areas=())
    nav.next_target(START, AIM)
    current[0] = source(box(244.15, 244.3, 88.2, 88.35, (11., 15.)))
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_height_layer_change_does_not_keep_old_adjusted_goal():
    nav = navigator()
    nav.next_target(START, AIM)
    upper_start, upper_aim = (START[0], 17., START[2]), (AIM[0], 17., AIM[2])
    assert nav.next_target(upper_start, upper_aim) == upper_aim


def test_scene_loss_cannot_reuse_the_alternative():
    current = [source()]
    nav = GuidanceNavigator(lambda: current[0], respect_water=False, water_areas=())
    nav.next_target(START, AIM)
    current[0] = None
    with pytest.raises(NavigationWait) as exc:
        nav.next_target(START, AIM)
    assert exc.value.reason == 'scene'


def test_quantized_movement_still_checks_full_clearance():
    from utils.core.coordinate_route import key_vector
    nav = navigator()
    selected = nav.next_target(START, AIM)
    basis = {'forward': [0., 1.], 'right': [1., 0.]}
    keys = nav.steering_keys(START, selected, basis, .3)
    assert keys
    vector = key_vector(keys, basis)
    norm = math.hypot(*vector)
    end = (START[0]+vector[0]/norm*.3, START[2]+vector[1]/norm*.3)
    assert polygon_distance((START[0], START[2]), end, RAIL) >= .6
    assert all(clearance == .6 for _, clearance in nav.obstacles.polygons)


def test_interruption_is_not_swallowed_by_candidate_search():
    nav = navigator()
    nav._refresh_obstacles(START, AIM)
    def stop():
        raise RuntimeError('stopped')
    with pytest.raises(RuntimeError, match='stopped'):
        nav._shore_target(START, AIM, stop)


@pytest.mark.parametrize('degrees', [0., 45., 90., 180., 270.])
def test_padding_nudge_is_not_specific_to_the_recorded_bridge_orientation(degrees):
    angle = math.radians(degrees)
    def rotate(p):
        return (p[0]*math.cos(angle)-p[1]*math.sin(angle),
                p[0]*math.sin(angle)+p[1]*math.cos(angle))
    rail = [rotate(p) for p in [[-4., 0.], [4., 0.], [4., .04], [-4., .04]]]
    a, b = rotate((0., .8)), rotate((1., .635))
    src = scene({'polygon': rail, 'height': [-1., 2.], 'category': 'mountain'})
    nav = GuidanceNavigator(lambda: src, respect_water=False, water_areas=())
    start, aim = (a[0], 0., a[1]), (b[0], 0., b[1])
    selected = nav.next_target(start, aim)
    assert selected != aim and math.dist(selected, aim) < .03
    assert nav.obstacles.clear(a, (selected[0], selected[2]))
