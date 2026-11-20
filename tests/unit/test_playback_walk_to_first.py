"""「先走到首点」开关：默认关闭，开启后跳远距公交追踪改由本地寻路直达。"""
from unittest.mock import Mock

import pytest

from utils.core.coordinate_points import append_point, empty_points
from utils.ui.point_playback import PointPlayback


def points():
    stamp = '2026-10-11T23:30:00+08:00'
    return append_point(empty_points(), {'position': [1., 0., 2.], 'sample_at': stamp}, stamp)


def far_points():
    """首点放到很远的位置，触发首点距离大于 50 的分支。"""
    stamp = '2026-10-11T23:30:00+08:00'
    return append_point(empty_points(), {'position': [400., 0., 400.], 'sample_at': stamp}, stamp)


class FakeWindow:
    hwnd = 123

    def __init__(self, pid):
        self.pid = pid

    def size(self):
        return [1280, 720]


class WaitingControls:
    """一直停在 F6 等待状态，用于只验证启动参数。"""

    def __init__(self, window, size, feedback, stop_requested):
        self.stop_requested = stop_requested

    def start(self):
        pass

    def snapshot(self):
        return False, self.stop_requested()

    def close(self):
        pass


class RunningControls:
    """一启动即视为已按 F6，让执行流程走到首点距离判定。"""

    def __init__(self, window, size, feedback, stop_requested):
        self.stop_requested = stop_requested

    def start(self):
        pass

    def snapshot(self):
        return True, self.stop_requested()

    def close(self):
        pass


class PositionReader:
    def __init__(self, position):
        self.position = position

    def read_for_playback(self):
        return list(self.position)

    def close(self):
        pass


def test_walk_to_first_defaults_to_off():
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=WaitingControls, device_factory=lambda window: Mock())
    worker.start(points(), 77)
    assert worker.thread is not None
    worker.stop()
    worker.close()


@pytest.mark.parametrize('value', (1, 0, 'yes', None))
def test_walk_to_first_rejects_non_boolean(value):
    worker = PointPlayback('profile.json', runner=Mock())
    with pytest.raises(ValueError, match='先走到首点'):
        worker.start(points(), 77, walk_to_first=value)
    assert worker.thread is None


def test_walk_to_first_requires_auto_navigation():
    worker = PointPlayback('profile.json', runner=Mock())
    with pytest.raises(ValueError, match='自动寻路'):
        worker.start(points(), 77, auto_navigate=False, walk_to_first=True)
    assert worker.thread is None


def test_walk_to_first_skips_bus_guidance_and_hands_over_to_route(monkeypatch):
    """开启后不打开地图、不追公交，直接交给本地寻路走向首点。"""
    targeter = Mock()
    monkeypatch.setattr('utils.flows.map_targeting.MapTargeter', targeter, raising=False)
    runner = Mock(return_value=True)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
                           controls_factory=RunningControls, device_factory=lambda window: Mock(),
                           reader_factory=lambda *args, **kwargs: PositionReader([0., 0., 0.]),
                           scene_provider=Mock(), guidance_runner=Mock(return_value={'arrived': True}))
    worker.start(far_points(), 77, auto_navigate=True, walk_to_first=True)
    worker.thread.join(timeout=3)
    targeter.assert_not_called()
    assert runner.called
    states = [event.get('message', '') for event in worker.take_log_events()]
    assert any('跳过公交追踪' in message for message in states), states
    worker.close()
