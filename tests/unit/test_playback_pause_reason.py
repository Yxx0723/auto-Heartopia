from unittest.mock import Mock

from utils.drivers.timeline_hotkeys import PlaybackControls


def test_tool_failure_keeps_actual_pause_reason_without_claiming_lost_focus():
    window, feedback = Mock(), Mock()
    controls = PlaybackControls(window, [1024, 768], feedback)
    controls.running = True
    controls.pause_with_reason('未识别到斧镐图标')
    assert controls.snapshot() == (False, False)
    feedback.update.assert_called_once_with('paused', '未识别到斧镐图标', window=window)


def test_focus_pause_retains_focus_recovery_instructions():
    controls = PlaybackControls(Mock(), [1024, 768], Mock())
    controls.running = True
    controls.pause()
    assert not controls.running
    assert '游戏失去焦点' in controls.feedback.update.call_args.args[1]
