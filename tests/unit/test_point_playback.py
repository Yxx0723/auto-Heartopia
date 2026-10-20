"""The native playback worker reports errors and can stop before F6."""
from pathlib import Path
from datetime import datetime
import json
import threading
from unittest.mock import Mock

import pytest

from utils.core.coordinate_points import append_point, empty_points
from utils.ui.point_playback import PointPlayback
from utils.flows.coordinate_player import drive_points


def points():
    stamp = '2026-10-11T23:30:00+08:00'
    return append_point(empty_points(), {'position': [1., 0., 2.], 'sample_at': stamp}, stamp)


class FakeWindow:
    hwnd = 123

    def __init__(self, pid):
        self.pid = pid

    def size(self):
        return [1280, 720]


class WaitingControls:
    def __init__(self, window, size, feedback, stop_requested):
        self.stop_requested = stop_requested
        self.started = threading.Event()
        self.closed = False

    def start(self):
        self.started.set()

    def snapshot(self):
        return False, self.stop_requested()

    def close(self):
        self.closed = True


def test_stop_before_f6_closes_worker_without_opening_reader_or_input():
    readers, controls, devices = [], [], []
    def control_factory(*args, **kwargs):
        value = WaitingControls(*args, **kwargs)
        controls.append(value)
        return value
    def reader_factory(*args, **kwargs):
        readers.append(args)
        raise AssertionError('reader opened before F6')
    def device_factory(window):
        value = Mock()
        devices.append(value)
        return value
    worker = PointPlayback(Path('profile.json'), runner=drive_points, window_factory=FakeWindow,
                           controls_factory=control_factory, reader_factory=reader_factory,
                           device_factory=device_factory)
    worker.start(points(), 77)
    worker.stop()
    worker.close()
    assert not worker.active and not readers
    assert controls[0].closed
    devices[0].execute.assert_not_called()
    devices[0].release_all.assert_called()
    final = worker.take_latest()
    assert final['state'] == 'stopped'
    assert 'completed_at' not in final and '完成时间' not in final['message']


def test_worker_reports_binding_error_and_allows_retry():
    factory = Mock(side_effect=ValueError('游戏窗口不存在'))
    worker = PointPlayback('profile.json', runner=drive_points, window_factory=factory)
    worker.start(points(), 77)
    worker.thread.join(timeout=2)
    assert not worker.active
    assert worker.take_latest()['state'] == 'error'
    worker.start(points(), 77)
    worker.thread.join(timeout=2)
    assert factory.call_count == 2
    worker.close()


def test_worker_passes_nearest_mode_and_does_not_claim_all_points_completed():
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock())
    worker.start(points(), 77, start_mode='nearest')
    worker.thread.join(timeout=2)
    assert runner.call_args.kwargs['start_mode'] == 'nearest'
    assert runner.call_args.kwargs['recovery_attempts'] == 5
    assert runner.call_args.kwargs['jump_on_recovery'] is True
    assert runner.call_args.kwargs['continuous_recovery'] is True
    final = worker.take_latest()
    assert final['state'] == 'finished'
    assert final['message'].startswith('本次点位回放完成 · 完成时间 ')
    worker.close()


def test_each_completed_run_displays_and_logs_its_actual_finish_time(tmp_path):
    timestamps = [datetime.fromisoformat('2026-10-19T23:59:59+08:00'),
                  datetime.fromisoformat('2026-10-20T00:00:12+08:00')]
    wall_clock = Mock(side_effect=timestamps)
    log_path = tmp_path / 'playback.jsonl'
    worker = PointPlayback('profile.json', runner=Mock(return_value=True), window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock(),
                           wall_clock=wall_clock, diagnostics_path=log_path)
    for loop_count, timestamp in zip((3, 1), timestamps):
        worker.start(points(), 77, loop_count=loop_count)
        worker.thread.join(timeout=2)
        final = worker.take_latest()
        prefix = '已完成 3 轮回放' if loop_count == 3 else '本次点位回放完成'
        assert final['state'] == 'finished'
        assert final['message'] == prefix + ' · 完成时间 ' + timestamp.strftime('%Y-%m-%d %H:%M:%S')
        assert final['completed_at'] == timestamp.isoformat(timespec='seconds')
    worker.close()
    entries = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines()]
    finished = [entry for entry in entries if entry['state'] == 'finished']
    assert [entry['completed_at'] for entry in finished] == [t.isoformat(timespec='seconds') for t in timestamps]
    assert wall_clock.call_count == 2


def test_worker_passes_selected_recovery_count_and_jump_to_runner():
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock())
    worker.start(points(), 77, recovery_attempts=7, jump_on_recovery=True)
    worker.thread.join(timeout=2)
    assert runner.call_args.kwargs['recovery_attempts'] == 7
    assert runner.call_args.kwargs['jump_on_recovery'] is True
    worker.close()


@pytest.mark.parametrize('count,jump', [(0, False), (-1, False), (11, False), (True, False), (3, 1)])
def test_worker_rejects_invalid_options_before_creating_thread(count, jump):
    factory = Mock()
    worker = PointPlayback('profile.json', runner=Mock(), window_factory=factory)
    with pytest.raises(ValueError):
        worker.start(points(), 77, recovery_attempts=count, jump_on_recovery=jump)
    factory.assert_not_called()
    assert worker.thread is None


def test_worker_passes_collection_wait_to_runner():
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock())
    worker.start(points(), 77, collect_wait_seconds=4.5)
    worker.thread.join(timeout=2)
    assert runner.call_args.kwargs['collect_wait_seconds'] == 4.5
    worker.close()


def test_worker_passes_normal_repair_controller_when_option_enabled():
    runner, repair_factory = Mock(return_value=True), Mock()
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock(),
                           repair_factory=repair_factory)
    worker.start(points(), 77, auto_repair=True)
    worker.thread.join(timeout=2)
    repair_factory.assert_called_once_with(Path('repair_box_menu.png'))
    assert runner.call_args.kwargs['repair_box'] is repair_factory.return_value
    worker.close()


def test_worker_does_not_build_repair_controller_when_disabled():
    runner, repair_factory = Mock(return_value=True), Mock()
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock(),
                           repair_factory=repair_factory)
    worker.start(points(), 77, auto_repair=False)
    worker.thread.join(timeout=2)
    repair_factory.assert_not_called()
    assert 'repair_box' not in runner.call_args.kwargs
    assert runner.call_args.kwargs['tool_selector'].menu is None
    worker.close()


def test_worker_rejects_non_boolean_auto_repair_before_start():
    worker = PointPlayback('profile.json', runner=Mock())
    with pytest.raises(ValueError, match='自动维修'):
        worker.start(points(), 77, auto_repair=1)
    assert worker.thread is None


def test_food_controller_is_lazy_and_independent_of_auto_repair():
    runner, factory = Mock(return_value=True), Mock()
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock(),
                           food_factory=factory)
    worker.start(points(), 77, auto_eat=True, auto_repair=False)
    worker.thread.join(timeout=2)
    factory.assert_not_called()
    assert runner.call_args.kwargs['food'].menu is None
    assert 'repair_box' not in runner.call_args.kwargs
    worker.close()


def test_worker_rejects_invalid_auto_food_before_start():
    worker = PointPlayback('profile.json', runner=Mock())
    with pytest.raises(ValueError, match='吃水果'):
        worker.start(points(), 77, auto_eat=1)
    assert worker.thread is None


def test_worker_navigation_is_lazy_before_f6_and_uses_the_bound_process():
    provider = Mock()
    worker = PointPlayback('profile.json', runner=drive_points, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock(),
                           scene_provider=provider)
    worker.start(points(), 77, auto_navigate=True)
    worker.stop()
    worker.close()
    provider.assert_not_called()
    assert worker.take_latest()['state'] == 'stopped'


def test_worker_passes_navigation_with_fresh_scene_provider_to_runner():
    provider = Mock(return_value={'state': 'ready', 'objects': [], 'colliders': []})
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock(),
                           scene_provider=provider)
    worker.start(points(), 77, auto_navigate=True)
    worker.thread.join(timeout=2)
    navigator = runner.call_args.kwargs['navigator']
    assert navigator.scene_provider() is provider.return_value
    provider.assert_called_once_with(77)
    worker.close()


@pytest.mark.parametrize('enabled', [True, False])
def test_worker_attaches_persistent_path_memory_only_when_enabled(tmp_path, enabled):
    provider = Mock(return_value={'state': 'ready', 'objects': [], 'colliders': []})
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock(),
                           scene_provider=provider)
    worker.start(points(), 77, auto_navigate=True, route_path=tmp_path/'route.json', remember_paths=enabled)
    worker.thread.join(timeout=2)
    memory = runner.call_args.kwargs['navigator'].path_memory
    assert (memory is not None) == enabled
    if enabled:
        assert memory.path == tmp_path/'路线记忆'/'route.json'
    worker.close()


def test_worker_keeps_planned_path_across_status_updates_and_clears_it_on_stop():
    worker = PointPlayback('profile.json', runner=Mock())
    path = [[1., 0., 2.], [2., 0., 3.]]
    worker.update('playing', 'navigation', navigation_path=path)
    worker.update('playing', 'progress update')
    assert worker.take_latest()['navigation_path'] == path
    worker.update('collecting', 'collection')
    assert worker.take_latest()['navigation_path'] == []
    worker.update('playing', 'navigation', navigation_path=path)
    worker.update('stopped', 'stop')
    assert worker.take_latest()['navigation_path'] == []


def test_worker_rejects_navigation_without_scene_source_before_starting_thread():
    worker = PointPlayback('profile.json', runner=Mock())
    with pytest.raises(ValueError, match='场景导航'):
        worker.start(points(), 77, auto_navigate=True)
    assert worker.thread is None


@pytest.mark.parametrize('seconds', [-1, 301, True, float('nan')])
def test_worker_rejects_invalid_collection_wait_before_thread_start(seconds):
    worker = PointPlayback('profile.json', runner=Mock(), window_factory=Mock())
    with pytest.raises(ValueError):
        worker.start(points(), 77, collect_wait_seconds=seconds)
    assert worker.thread is None
