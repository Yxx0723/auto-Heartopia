"""Dynamic targets are re-read; exhausted or missing targets never receive F."""
from unittest.mock import Mock
import pytest
from tests.unit.test_coordinate_player import Game
from utils.flows.coordinate_player import _Follower, _Stopped, _Paused
from utils.core.coordinate_points import ordered_waypoints, empty_points, append_point
from utils.ui.native_coordinate_monitor import CoordinateWindow
from utils.ui.monitor_style import SCENE_CATEGORIES
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


def test_confirmed_counter_drop_stops_the_loop_on_the_first_press():
    f, game, point, target, _ = follower()
    point['special_resource']['press_count'] = 10

    def on_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            target['resource_available'] = 1

    game.on_input = on_input
    assert f.collect_point(point, 0, 1) is True
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 1
    assert '已确认采到' in f._report.call_args_list[-1].args[1]


def test_a_target_vanishing_after_a_press_ends_as_collected_not_as_an_error():
    f, game, point, target, nav = follower()
    point['special_resource']['press_count'] = 10

    def on_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            nav.scene_provider.return_value['objects'] = []

    game.on_input = on_input
    assert f.collect_point(point, 0, 1) is True
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 1
    assert '资源已从场景消失' in f._report.call_args_list[-1].args[1]


def test_presses_that_change_nothing_stop_at_the_policy_limit():
    f, game, point, _, _ = follower()
    point['special_resource']['press_count'] = 10
    results = [f.collect_point(point, 0, 1) for _ in range(3)]
    assert results == [False, False, True]
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 3
    assert '资源状态无变化' in f._report.call_args_list[-1].args[1]


def test_an_unreadable_scene_after_a_press_never_claims_a_drop():
    f, game, point, _, nav = follower()
    point['special_resource']['press_count'] = 1

    def on_input(event):
        if event == {'type': 'key_down', 'key': 'f'}:
            nav.scene_provider.return_value['state'] = 'loading'

    game.on_input = on_input
    assert f.collect_point(point, 0, 1) is False
    assert f.collect_point(point, 0, 1) is True
    assert '未能读到资源状态' in f._report.call_args_list[-1].args[1]


def test_a_press_only_happens_inside_the_interaction_envelope():
    f, game, point, target, _ = follower()
    game.position = [1.3, 0., 0.]
    target['position'] = [0., 0., 0.]
    point['position'] = [0., 0., 0.]
    assert not f.collect_point(point, 0, 1)
    assert not game.events


def global_hint(target):
    return {k:v for k,v in target.items() if k!='resource_available'} | {
        'resource_global_validated':True,'resource_global_only':True}


def test_global_position_can_guide_navigation_but_never_authorize_f():
    f,game,point,target,nav=follower()
    hint=global_hint(target)
    nav.scene_provider.return_value={'state':'ready','objects':[],
        'global_special_state':'ready','global_special_resources':[hint]}
    assert f.special_target(point,allow_global=True) is hint
    with pytest.raises(ValueError):f.collect_point(point,0,1)
    assert not game.events


def test_arrival_waits_for_live_object_before_any_f_press():
    f,game,point,target,nav=follower()
    hint=global_hint(target)
    def scene():
        return {'state':'ready','objects':[target] if game.time>=.6 else [],
            'global_special_state':'ready','global_special_resources':[hint]}
    nav.scene_provider.side_effect=scene
    f.calibrate=Mock(side_effect=AssertionError('No calibration at resource'))
    assert f.follow()
    assert min(t for t,e in game.events if e.get('key')=='f')>=.6
    assert all(e['key']=='f' for _,e in game.events)


def test_arrival_without_model_has_bounded_wait_and_no_input(monkeypatch):
    from utils.core import thresholds as policy
    f,game,point,target,nav=follower()
    nav.scene_provider.return_value={'state':'ready','objects':[],
        'global_special_state':'ready','global_special_resources':[global_hint(target)]}
    monkeypatch.setattr(policy,'COORD_SPECIAL_LOAD_WAIT_SECONDS',.4)
    with pytest.raises(ValueError,match='仍未加载'):f.follow()
    assert not game.events and not game.keys

@pytest.mark.parametrize('historical',[False,True])
def test_far_resource_navigation_loads_live_object_before_collecting(historical):
    f,game,point,target,_=follower()
    game.position=[5.,0.,0.]
    hint=global_hint(target)
    field='global_special_resources'
    if historical:
        hint.pop('resource_global_validated')
        hint['resource_search_hint']=True
        field='special_search_hints'
    def scene():
        return {'state':'ready','objects':[target] if game.position[0]<3 else [],
                'global_special_state':'ready',field:[hint],'colliders':[]}
    f.navigator=LocalNavigator(scene,clock=game.monotonic,water_areas=())
    def on_input(event):
        if event=={'type':'key_down','key':'f'}:
            assert in_interaction_range(game.position,target['position'])
            assert scene()['objects']
    game.on_input=on_input
    assert f.follow()
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==2


def test_transient_scene_loss_waits_and_resumes_without_stopping_or_pressing_early():
    f,game,point,target,nav=follower()
    nav.scene_provider.side_effect=lambda: {'state':'loading'} if game.time<.8 else {'state':'ready','objects':[target]}
    f.calibrate=Mock(side_effect=AssertionError('No movement while waiting at target'))
    assert f.follow()
    assert min(t for t,e in game.events if e.get('key')=='f')>=.8
    assert all(e['key']=='f' for _,e in game.events)


def test_stop_remains_available_while_scene_is_unavailable():
    f,game,point,target,nav=follower()
    game.stop_at=.5
    nav.scene_provider.return_value={'state':'loading'}
    with pytest.raises(_Stopped):f.follow()
    assert not game.events and not game.keys
