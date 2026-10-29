"""Offline shore-padding regression; these do not prove in-game walkability."""
import math

import pytest

from tests.unit.test_coordinate_navigation import scene, box
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait
from utils.core.guidance_navigation import GuidanceNavigator
from utils.core.navigation_water import NAVIGATION_WATER_AREAS

START = (-5.1186137199401855, 20.049985885620117, 170.19007873535156)
AIM = (-4.026836102635894, START[1], 169.70166796629871)
WATER = (NAVIGATION_WATER_AREAS[23],)


def test_recorded_padding_goal_gets_checked_dry_side_alternative():
    nav = GuidanceNavigator(lambda: scene(), water_areas=WATER)
    result = nav.next_target(START, AIM)
    assert result != AIM
    assert math.dist(result, AIM) < .25
    assert result[1] == AIM[1]
    assert nav.obstacles.clear((START[0], START[2]), (result[0], result[2]))
    assert not nav.obstacles.clear((AIM[0], AIM[2]), (AIM[0], AIM[2]))
    assert nav.last_decision['kind'] == 'guidance_shore_alternative'
    assert nav.last_decision['requested_target'] == AIM


def test_ordinary_playback_keeps_original_padding_rejection():
    nav = LocalNavigator(lambda: scene(), water_areas=WATER)
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_checked_alternative_does_not_skip_an_actual_wall():
    source = scene(box(-20, 20, 169.85, 169.9, (19, 23)))
    nav = GuidanceNavigator(lambda: source, water_areas=WATER)
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_target_inside_real_water_is_never_projected():
    nav = GuidanceNavigator(lambda: scene(), water_areas=WATER)
    with pytest.raises(NavigationWait):
        nav.next_target(START, (-2.9, START[1], 170.))


def test_current_point_in_padding_does_not_authorize_a_substitute():
    nav = GuidanceNavigator(lambda: scene(), water_areas=WATER)
    with pytest.raises(NavigationWait):
        nav.next_target((-4.1, START[1], 170.), AIM)


def test_repeat_query_reuses_path_but_new_wall_invalidates_alternative():
    source = [scene()]
    nav = GuidanceNavigator(lambda: source[0], water_areas=WATER)
    first = nav.next_target(START, AIM)
    plans = nav.stats['plans']
    assert nav.next_target(START, AIM) == first
    assert nav.stats['plans'] == plans
    source[0] = scene(box(-20, 20, 169.85, 169.9, (19, 23)))
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_scene_loss_stops_even_after_a_valid_alternative():
    source = [scene()]
    nav = GuidanceNavigator(lambda: source[0], water_areas=WATER)
    nav.next_target(START, AIM)
    source[0] = None
    with pytest.raises(NavigationWait) as error:
        nav.next_target(START, AIM)
    assert error.value.reason == 'scene'


def test_original_clear_goal_and_height_are_unchanged():
    nav = GuidanceNavigator(lambda: scene(), water_areas=WATER)
    target = (-5.5, START[1], 171.)
    assert nav.next_target(START, target) == target


def test_stop_callback_propagates_during_fallback():
    def stop():
        raise RuntimeError('F9')
    nav = GuidanceNavigator(lambda: scene(), water_areas=WATER)
    with pytest.raises(RuntimeError, match='F9'):
        nav.next_target(START, AIM, check=stop)


def test_projection_stays_within_existing_guide_corridor(monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_GUIDANCE_CORRIDOR_RADIUS', .05)
    nav = GuidanceNavigator(lambda: scene(), water_areas=WATER)
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_physical_target_obstacle_cannot_be_reclassified_as_water_padding():
    source = scene(box(-4.2, -3.9, 169.5, 169.8, (19, 23)))
    nav = GuidanceNavigator(lambda: source, water_areas=WATER)
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_learned_risk_is_not_ignored_by_alternative():
    nav = GuidanceNavigator(lambda: scene(), water_areas=WATER)
    nav.next_target(START, AIM)
    nav.obstacles.risk_cost = lambda a, b: 1.
    assert nav._shore_target(START, AIM, lambda: None) is None


def test_cached_alternative_does_not_survive_a_height_layer_change():
    source = scene(box(-20, 20, 169.85, 169.9, (23, 26)))
    nav = GuidanceNavigator(lambda: source, water_areas=WATER)
    nav.next_target(START, AIM)
    with pytest.raises(NavigationWait):
        nav.next_target((START[0], 23.5, START[2]), (AIM[0], 23.5, AIM[2]))


def test_no_candidate_within_all_water_clearances_keeps_waiting():
    # A second water body closes off the narrow dry gap; neither margin is reduced.
    other = [[-10, 169], [-5.15, 169], [-5.15, 169.9], [-10, 169.9]]
    nav = GuidanceNavigator(lambda: scene(), water_areas=(*WATER, other))
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_shore_alternative_never_rewrites_cursor_destination_or_arrival():
    from tests.unit.test_native_guidance_follow import snapshot
    from utils.core.guidance_path import GuidanceCursor, validate_guidance
    cursor = GuidanceCursor(validate_guidance(snapshot(path=(START, AIM), target=AIM), 0.), START)
    before = (cursor.path, cursor.target, cursor.revision)
    nav = GuidanceNavigator(lambda: scene(), water_areas=WATER)
    selected = nav.next_target(START, cursor.aim(START)['position'])
    assert selected != AIM
    assert (cursor.path, cursor.target, cursor.revision) == before
    assert not cursor.aim(START)['arrived']
    # Arrival must still be measured to the ORIGINAL path end, not a new goal.
    far = (AIM[0]-2., AIM[1], AIM[2])
    assert not cursor.aim(far)['arrived']


def test_offline_follower_finishes_short_original_line_without_interaction():
    from tests.unit.test_native_guidance_follow import guide_game, run, snapshot
    game = guide_game()
    game.position = list(START)
    game.guidance = lambda: snapshot(path=(START, AIM), target=AIM, now=game.time)
    nav = GuidanceNavigator(lambda: scene(), clock=game.monotonic, water_areas=WATER)
    result = run(game, navigator=nav)
    assert result['arrived']
    assert math.dist(game.position, AIM) <= .8
    assert not game.keys
    assert not any(e['key'] in ('f', 'b', 't') for _, e in game.events)
