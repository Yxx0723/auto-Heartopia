"""The native playback worker reports errors and can stop before F6."""
from pathlib import Path
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
    assert worker.take_latest()['state'] == 'stopped'


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
    final = worker.take_latest()
    assert final['state'] == 'finished'
    assert final['message'] == '本次点位回放完成'
    worker.close()


def test_worker_passes_selected_recovery_count_and_jump_to_runner():
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock())
    worker.start(points(), 77, recovery_attempts=7, jump_on_recovery=True)
    worker.thread.join(timeout=2)
    assert runner.call_args.kwargs['recovery_attempts'] == 7
    assert runner.call_args.kwargs['jump_on_recovery'] is True
    worker.close()


@pytest.mark.parametrize('count,jump', [(-1, False), (11, False), (True, False), (3, 1)])
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


@pytest.mark.parametrize('seconds', [-1, 301, True, float('nan')])
def test_worker_rejects_invalid_collection_wait_before_thread_start(seconds):
    worker = PointPlayback('profile.json', runner=Mock(), window_factory=Mock())
    with pytest.raises(ValueError):
        worker.start(points(), 77, collect_wait_seconds=seconds)
    assert worker.thread is None
