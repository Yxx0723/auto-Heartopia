"""Dynamic targets are re-read; exhausted or missing targets never receive F."""
from unittest.mock import Mock
import pytest
from tests.unit.test_coordinate_player import Game
from utils.flows.coordinate_player import _Follower, _Stopped, _Paused
from utils.core.coordinate_points import ordered_waypoints, empty_points, append_point
from utils.ui.native_coordinate_monitor import CoordinateWindow, SCENE_CATEGORIES
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap
from utils.core.special_approach import interaction_standpoint, in_interaction_range


def follower():
    game = Game()
    target = {'category': 'fluorite', 'resource_static_id': 42002, 'resource_available': 2,
              'map_dynamic': True, 'position': [0., 0., 0.]}
    nav = Mock()
    nav.scene_provider.return_value = {'state': 'ready', 'objects': [target]}
    point = {'name': 'fluorite', 'position': [0., 0., 0.],
             'special_resource': {'category': 'fluorite', 'static_id': 42002, 'press_count': 2}}
    f = _Follower({'duration': 1000, 'client_size': game.size()}, game, game, game, game, Mock(), game,
                  waypoints=[point], collect_wait_seconds=0, navigator=nav)
    return f, game, point, target, nav


def test_repeated_presses_stop_at_selected_count_even_when_resource_counter_does_not_change():
    f, game, point, target, _ = follower()
    for count in (2, 2):
        target['resource_available'] = count
        assert f.collect_point(point, 0, 1) is False
        assert not game.keys
    assert f.collect_point(point, 0, 1) is True
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 2


@pytest.mark.parametrize('fault', ['missing', 'cached', 'wrong_id', 'ambiguous', 'not_ready'])
def test_unconfirmed_dynamic_target_never_receives_input(fault):
    f, game, point, target, nav = follower()
    if fault == 'missing': nav.scene_provider.return_value['objects'] = []
    if fault == 'cached': target['map_cached'] = True
    if fault == 'wrong_id': target['resource_static_id'] += 1
    if fault == 'ambiguous': nav.scene_provider.return_value['objects'].append(dict(target))
    if fault == 'not_ready': nav.scene_provider.return_value['state'] = 'loading'
    with pytest.raises(ValueError): f.collect_point(point, 0, 1)
    assert not game.events


def test_moving_resource_requires_approach_again_before_any_press():
    f, game, point, target, _ = follower()
    target['position'] = [10., 0., 0.]
    assert not f.collect_point(point, 0, 1)
    assert not game.events


def test_zero_availability_releases_keys_without_claiming_collection_success():
    f, game, point, _, _ = follower()
    f.navigator.scene_provider.return_value['objects'][0]['resource_available'] = 0
    with pytest.raises(ValueError): f.collect_point(point, 0, 1)
    assert not game.keys and not game.events


@pytest.mark.parametrize('control', ['stop_at', 'pause_at'])
def test_interrupt_during_a_press_always_releases_f(control):
    f, game, point, _, _ = follower()
    setattr(game, control, .01)
    with pytest.raises((_Stopped, _Paused)): f.collect_point(point, 0, 1)
    assert not game.keys


def test_dynamic_route_target_survives_validation_and_ordering():
    stamp = '2026-10-20T17:00:00+08:00'
    data = append_point(empty_points(), {'position': [1., 2., 3.], 'sample_at': stamp}, stamp)
    data['points'][0]['special_resource'] = {'category': 'fluorite', 'static_id': 42002}
    assert ordered_waypoints(data)[0]['special_resource'] == data['points'][0]['special_resource']


def test_special_resource_icons_and_categories_are_distinct():
    canvas = Mock()
    for category in ('fluorite', 'roaming_oak'):
        CoordinateWindow.draw_scene_symbol(canvas, 20, 30, category)
        assert SCENE_CATEGORIES[category][0]
    tags = {call.kwargs.get('tags') for call in canvas.create_polygon.call_args_list + canvas.create_oval.call_args_list}
    assert {'scene-fluorite', 'scene-roaming_oak'} <= tags


def test_failed_live_oak_position_collects_without_calibration_or_trying_to_enter_the_trunk():
    f, game, point, target, _ = follower()
    game.position = [-116.01211547851562, 19.914779663085938, -47.84819030761719]
    target['position'] = [-116.87000274658203, 19.907392501831055, -47.41999816894531]
    point['position'] = list(target['position'])
    f.calibrate = Mock(side_effect=AssertionError('Must not walk calibration beside the resource'))
    assert f.follow()
    f.calibrate.assert_not_called()
    downs = [e['key'] for _, e in game.events if e['type'] == 'key_down']
    assert downs == ['f', 'f']
    assert not game.keys


def test_far_approach_navigates_to_free_standpoint_and_preserves_solid_collision():
    f, game, point, target, _ = follower()
    game.position = [5., 0., 0.]
    scene = {'state': 'ready', 'objects': [target], 'colliders': [
        {'polygon': [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]], 'height': [0., 2.]}]}
    f.navigator = LocalNavigator(lambda: scene, clock=game.monotonic, water_areas=())
    assert f.follow()
    assert .5 < (game.position[0]**2+game.position[2]**2)**.5 <= 1.25
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 2
    assert not any(e.get('key') == 'space' for _, e in game.events)


def test_no_free_ring_does_not_select_the_occupied_resource_center():
    scene = {'colliders': [{'polygon': [[-2,-2],[2,-2],[2,2],[-2,2]], 'height': [0,2]}]}
    obstacles = ObstacleMap(scene, [0,0,0], water_areas=())
    with pytest.raises(ValueError, match='交互位置'):
        interaction_standpoint([5,0,0], [0,0,0], obstacles)


def test_horizontal_proximity_never_allows_collection_on_another_floor():
    assert not in_interaction_range([0., 2., 0.], [0., 0., 0.])
