"""UI frame cadence is independent from memory sampling and never catches up in bursts."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from utils.core import thresholds as policy
from utils.ui.native_coordinate_monitor import CoordinateWindow

@pytest.mark.parametrize('render_seconds,expected_ms', [(0., 17), (.006, 11), (.1, 1)])
def test_ui_frame_budget_subtracts_render_cost_without_spinning(render_seconds, expected_ms):
    ui = CoordinateWindow.__new__(CoordinateWindow)
    now = [0.]
    ui.closed = False
    ui.clock = lambda: now[0]
    ui.root = Mock()
    ui.worker = SimpleNamespace(take_latest=lambda: None)
    ui.scene_worker = None
    ui.playback = SimpleNamespace(take_latest=lambda: None)
    ui.view = SimpleNamespace(expire=Mock())
    ui.render = lambda: now.__setitem__(0, render_seconds)
    sampling_period = policy.COORD_MONITOR_REFRESH_SECONDS
    ui.tick()
    ui.root.after.assert_called_once_with(expected_ms, ui.tick)
    assert policy.COORD_UI_FRAME_SECONDS == pytest.approx(1/60)
    assert policy.COORD_MONITOR_REFRESH_SECONDS == sampling_period
    assert policy.COORD_SCENE_REFRESH_SECONDS == .2
