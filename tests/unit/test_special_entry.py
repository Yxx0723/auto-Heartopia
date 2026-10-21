"""The real worker/runner boundary accepts scene refresh and reports missing resources precisely."""
from unittest.mock import Mock
import pytest
from tests.unit.test_point_playback import FakeWindow, points
from utils.ui.point_playback import PointPlayback
from utils.flows import coordinate_player
from utils.core.special_approach import select_special_target
from utils.ui.native_coordinate_monitor import CoordinateWindow


def test_real_worker_and_real_runner_forward_refresh_to_follower(monkeypatch):
    follower = Mock()
    follower.return_value.follow.return_value = True
    monkeypatch.setattr(coordinate_player, '_Follower', follower)
    control = Mock()
    control.snapshot.return_value = (True, False)
    refresh = Mock()
    worker = PointPlayback('profile.json', runner=coordinate_player.drive_points,
        window_factory=FakeWindow, controls_factory=lambda *a: control,
        device_factory=lambda *a: Mock(), reader_factory=lambda *a, **kw: Mock(),
        scene_provider=lambda pid: {'state': 'ready', 'objects': [], 'colliders': []},
        scene_refresh=refresh)
    worker.start(points(), 77, auto_navigate=True)
    worker.thread.join(timeout=2)
    assert not worker.active
    assert worker.take_latest()['state'] == 'finished'
    follower.call_args.kwargs['scene_refresh']()
    refresh.assert_called_once_with(77)
    worker.close()


def test_one_refresh_method_handles_button_and_playback_callback():
    window = CoordinateWindow.__new__(CoordinateWindow)
    window.scene_worker, window.closed = Mock(), False
    window.refresh_scene()
    window.refresh_scene(77)
    assert window.scene_worker.refresh.call_count == 2
    window.closed = True
    window.refresh_scene(77)
    assert window.scene_worker.refresh.call_count == 2


def resource(category='fluorite', **extra):
    return {'category': category, 'position': [0., 0., 0.], 'map_dynamic': True,
            'resource_available': 2, **extra}


@pytest.mark.parametrize('objects,phrase', [
    ([resource('roaming_oak')], '溜溜橡木'),
    ([], '尚未加载无暇萤石'),
    ([resource(map_cached=True)], '尚未加载'),
    ([resource(resource_available=0)], '采集标志不可用'),
    ([resource(resource_available=None)], '尚未读完整'),
    ([resource(), resource()], '多个无暇萤石')])
def test_missing_unavailable_and_ambiguous_are_distinct(objects, phrase):
    with pytest.raises(ValueError, match=phrase):
        select_special_target({'state': 'ready', 'objects': objects}, 'fluorite', '无暇萤石')


def test_only_a_live_available_resource_is_selected():
    obj = resource()
    assert select_special_target({'state': 'ready', 'objects': [obj]}, 'fluorite', '无暇萤石') is obj
    with pytest.raises(ValueError, match='尚未读取完成'):
        select_special_target({'state': 'loading'}, 'fluorite', '无暇萤石')


def test_verified_global_target_can_start_navigation_only():
    obj=resource()
    obj.pop('resource_available')
    obj.update(resource_global_validated=True,resource_global_only=True,resource_static_id=42002)
    scene={'state':'ready','objects':[],'global_special_state':'ready','global_special_resources':[obj]}
    assert select_special_target(scene,'fluorite','无暇萤石') is obj
    scene['global_special_state']='unavailable'
    with pytest.raises(ValueError):select_special_target(scene,'fluorite','无暇萤石')


def test_native_gather_button_starts_patrol_even_when_daily_target_is_missing(tmp_path, monkeypatch):
    from tests.unit.test_native_coordinate_monitor import capture_window
    window = capture_window(tmp_path)
    window.view.status['pid'] = 77
    window.navigation_scene = Mock(return_value={'state': 'ready', 'objects': [], 'global_special_state': 'ready',
        'global_resources': [{'category': 'tree', 'map_definition': True, 'position': [10., 20., 30.]}]})
    window.special_press_var = Mock(); window.special_press_var.get.return_value = '3'
    error = Mock(); monkeypatch.setattr('utils.ui.monitor_controller.messagebox.showerror', error)
    window.start_special_gather('roaming_oak')
    error.assert_not_called()
    args, kwargs = window.playback.start.call_args
    assert args[0]['special_patrol']['checkpoints'] == [[10., 20., 30.]]
    assert kwargs['remember_paths'] is False and kwargs['auto_navigate'] is True
    assert '巡查' in window.playback_status['message']


def test_worker_dispatches_patrol_and_keeps_not_found_distinct_from_collection(monkeypatch):
    from tests.unit.test_special_patrol_flow import data
    from utils.flows import special_patrol
    def patrol(*args, **kw):
        args[6]('patrol_finished', 'not found', resource_outcome='not_found')
        return True
    patrol_spy = Mock(side_effect=patrol)
    monkeypatch.setattr(special_patrol, 'drive_special_patrol', patrol_spy)
    normal = coordinate_player.drive_points
    worker = PointPlayback('profile.json', runner=normal, window_factory=FakeWindow,
        controls_factory=lambda *a: Mock(), device_factory=lambda *a: Mock(), scene_provider=lambda pid: {})
    worker.start(data([[1., 0., 2.]]), 77, auto_navigate=True)
    worker.thread.join(timeout=2)
    assert patrol_spy.call_count == 1 and patrol_spy.call_args.kwargs['point_runner'] is normal
    final = worker.take_latest()
    assert final['state'] == 'finished' and '不代表全图没有' in final['message']
    assert '采集成功' not in final['message']
    worker.close()
