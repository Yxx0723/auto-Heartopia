"""Recorded cases and synthetic clocks, never real bridge traversal validation."""
import json
from pathlib import Path

import pytest

from tests.unit.test_coordinate_navigation import scene, box
from tests.unit.test_guidance_padding import START, AIM, RAIL
from utils.core.coordinate_navigation import NavigationWait, polygon_distance
from utils.core.guidance_navigation import GuidanceNavigator
from utils.core.terrain_map import terrain_walls


CASES = json.loads(Path(__file__).with_name('guidance_bridge_cases.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['at'])
def test_all_recorded_bridge_stops_find_a_checked_connection(case):
    source = scene(*terrain_walls())
    nav = GuidanceNavigator(lambda: source, respect_water=False, water_areas=())
    p, target = tuple(case['position']), tuple(case['aim'])
    selected = nav.next_target(p, target)
    assert nav.obstacles.clear((p[0], p[2]), (selected[0], selected[2]), escape=True)


def test_only_padding_start_can_exit_monotonically_toward_checked_alternative():
    source = scene({'polygon': RAIL, 'height': [11., 15.], 'category': 'mountain'})
    case = CASES[0]
    nav = GuidanceNavigator(lambda: source, respect_water=False, water_areas=())
    p, aim = tuple(case['position']), tuple(case['aim'])
    selected = nav.next_target(p, aim)
    a, b = (p[0], p[2]), (selected[0], selected[2])
    assert polygon_distance(a, a, RAIL) < .6
    assert polygon_distance(a, b, RAIL) >= polygon_distance(a, a, RAIL)-1e-8
    assert polygon_distance(b, b, RAIL) >= .6


def test_actual_body_start_and_body_target_never_get_escape_permission():
    source = scene({'polygon': RAIL, 'height': [11., 15.], 'category': 'mountain'})
    center = (sum(p[0] for p in RAIL)/4, START[1], sum(p[1] for p in RAIL)/4)
    for start, target in [(center, AIM), (START, center)]:
        nav = GuidanceNavigator(lambda: source, respect_water=False, water_areas=())
        with pytest.raises(NavigationWait):
            nav.next_target(start, target)


def test_new_live_wall_over_bridge_aim_is_still_a_blocker():
    case = CASES[0]
    p, target = tuple(case['position']), tuple(case['aim'])
    wall = box(target[0]-.2, target[0]+.2, target[2]-.2, target[2]+.2, (11., 16.))
    wall.update(source='verified-native-world-collider', category='handrail')
    nav = GuidanceNavigator(lambda: scene(*terrain_walls(), wall), respect_water=False, water_areas=())
    with pytest.raises(NavigationWait):
        nav.next_target(p, target)


def test_collision_check_covers_configured_input_lease_not_only_two_ticks():
    from tests.unit.test_guidance_route_cache import cached_game
    from tests.unit.test_native_guidance_follow import run
    from utils.core import thresholds as policy
    game = cached_game()
    game.stop_at = .7
    nav = GuidanceNavigator(lambda: scene(), clock=game.monotonic, water_areas=())
    original, distances = nav.steering_keys, []
    def steering(position, target, basis, travel, **kwargs):
        distances.append(travel)
        return original(position, target, basis, travel, **kwargs)
    nav.steering_keys = steering
    run(game, navigator=nav)
    assert distances and min(distances) >= 3.*(
        policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS+policy.COORD_GUIDANCE_INPUT_POLL_SECONDS)
    assert not game.keys


@pytest.mark.parametrize('stop_at,arrived', [(20., True), (.5, False)])
def test_cached_bridge_path_closed_loop_without_manual_position_correction(stop_at, arrived):
    from tests.unit.test_native_guidance_follow import guide_game, run, snapshot
    path = json.loads(Path(__file__).with_name('guidance_bridge_path.json').read_text())['path']
    game = guide_game()
    game.position = list(CASES[0]['position'])
    game.stop_at = stop_at
    game.guidance = lambda: snapshot(path=path, target=(255.920944, 13.223556, 98.710358), now=game.time)
    # Deliberately FLAT synthetic floor: tests path/control behavior, not real
    # bridge support, swimming, ground height or physical walking capability.
    nav = GuidanceNavigator(lambda: scene(*terrain_walls()), clock=game.monotonic,
                             water_areas=(), respect_water=False)
    issues = []
    result = run(game, navigator=nav,
                 report=lambda state, message, **kw: issues.append(kw.get('navigation_wait_reason')))
    assert result['arrived'] is arrived
    assert 'no_path' not in issues
    assert not game.keys and game.closed
    assert not any(event['key'] in ('f', 'b', 't') for _, event in game.events)
