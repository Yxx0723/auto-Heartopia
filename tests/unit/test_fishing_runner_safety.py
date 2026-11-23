from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("win32gui")
from tools import fishing_auto_run as live


def make_runner(monkeypatch, *, held):
    runner = live.FishingRunner.__new__(live.FishingRunner)
    runner.state = lambda: live.policy.FISHING_STATE_HOOKED
    runner.last_game_state = None
    runner.can_throw = lambda: False
    runner.keep_front = lambda: True
    runner.check_input = Mock()
    runner.held = held
    runner.phase, runner.phase_at = "hooked", 0.
    runner.read_line = lambda: {"dom": "white"}
    runner.log = Mock()
    runner.device = Mock()
    monkeypatch.setattr(live, "time", SimpleNamespace(monotonic=lambda: 1., sleep=lambda _: None))
    return runner


def test_hooked_focus_loss_releases_button(monkeypatch):
    runner = make_runner(monkeypatch, held=True)
    runner.keep_front = lambda: False
    runner._tick()
    runner.device.execute.assert_called_once_with({"type": "mouse_up", "button": "left"})
    assert runner.phase == "idle" and not runner.held


def test_hook_timeout_prevents_repress(monkeypatch):
    runner = make_runner(monkeypatch, held=False)
    runner.phase_at = -live.policy.FISHING_HOOK_TIMEOUT_SECONDS
    runner._tick()
    runner.device.execute.assert_not_called()
    assert runner.phase == "idle" and not runner.held


def test_error_exits_and_closes_even_when_mouse_up_fails(monkeypatch, tmp_path):
    runner = make_runner(monkeypatch, held=True)
    runner.started, runner.duration, runner.pid = 0., 10., 123
    runner.log_path = tmp_path / "fishing.jsonl"
    runner.stop_requested = lambda: False
    runner.require_foreground = False
    runner.scene = Mock()
    runner.casts = 0
    runner._tick = Mock(side_effect=OSError("read error"))
    runner.device.execute.side_effect = OSError("button release error")
    with pytest.raises(OSError, match="button release error"):
        runner.run()
    runner.device.release_all.assert_called_once()
    runner.scene.close.assert_called_once()


def test_formal_fishing_waits_for_new_f6_press_in_game(monkeypatch, tmp_path):
    from utils.drivers.input_task_gate import InputTaskGate
    from utils.ui.fishing_control import FishingControl
    import win32api
    import win32con
    import win32gui

    # Held when armed, released, pressed while the monitor has focus, held
    # through a focus change, then released and pressed again in the game.
    pressed = iter([True, True, False, True, True, False, True])
    foreground = iter([20, 10])
    wait = Mock()
    key = lambda vk: 0x8000 if vk == win32con.VK_F6 and next(pressed) else 0
    monkeypatch.setattr(win32api, "GetAsyncKeyState", key)
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: next(foreground))
    monkeypatch.setattr(win32gui, "IsWindow", lambda hwnd: hwnd == 10)
    monkeypatch.setattr(live, "find_game", lambda: 10)
    runner = Mock(return_value=SimpleNamespace(casts=0, run=Mock()))
    monkeypatch.setattr(live, "FishingRunner", runner)
    control = FishingControl(tmp_path / "profile.json", tmp_path, InputTaskGate())
    monkeypatch.setattr(control.cancelled, "wait", wait)
    control.run()
    assert wait.call_count == 5
    runner.assert_called_once()
    runner.return_value.run.assert_called_once()


def test_f9_cancels_before_f6_without_opening_fishing_runner(monkeypatch, tmp_path):
    from utils.drivers.input_task_gate import InputTaskGate
    from utils.ui.fishing_control import FishingControl
    import win32api
    import win32con

    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda vk: 0x8000 if vk == win32con.VK_F9 else 0)
    monkeypatch.setattr(live, "find_game", lambda: 10)
    runner = Mock()
    monkeypatch.setattr(live, "FishingRunner", runner)
    control = FishingControl(tmp_path / "profile.json", tmp_path, InputTaskGate())
    control.run()
    runner.assert_not_called()
    assert control.message == "已取消自动钓鱼"


def test_fishing_runner_import_without_console_streams(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "sys.stdout = sys.stderr = None; "
            "from tools.fishing_auto_run import FishingRunner; "
            "assert FishingRunner.__name__ == 'FishingRunner'")
    method = [sys.executable, "-B", "-c", code, str(root)]
    result = subprocess.run(method, cwd=tmp_path, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert (tmp_path / "logs" / "日志文件.log").exists()


def test_aim_retries_then_casts_at_recently_confirmed_fish(monkeypatch, tmp_path):
    import numpy as np

    runner = make_runner(monkeypatch, held=True)
    runner.phase, runner.phase_at, runner.casts = "aiming", 0., 0
    runner.aim_fish = SimpleNamespace(cx=800., cy=300.)
    frame = np.zeros((807, 1382, 3), dtype=np.uint8)
    runner.aim_frame_shape = frame.shape[:2]
    runner.aim_sample_at, runner.aim_seen_at = 0., 4.6
    runner.grab = lambda: frame
    runner.fish_of = lambda frame, previous: None
    runner.out = tmp_path
    move, save = Mock(), Mock()
    monkeypatch.setattr(live, "move_mouse", move)
    monkeypatch.setattr(live, "find_ring", lambda *args, **kwargs: None)
    monkeypatch.setattr(live.cv2, "imwrite", save)
    events = []
    move.side_effect = lambda *args: events.append("aim")
    runner.device.execute.side_effect = lambda action: events.append(action["type"])
    now = SimpleNamespace(value=4.9)
    monkeypatch.setattr(live, "time", SimpleNamespace(monotonic=lambda: now.value, sleep=lambda _: None))

    runner._tick()
    assert runner.phase == "aiming" and runner.held
    assert events == []
    now.value = 5.0
    runner._tick()
    assert runner.phase == "waiting" and runner.casts == 1 and not runner.held
    assert events == ["aim", "mouse_up"]
    assert runner.log.call_args.kwargs["target_source"] == "最近一次确认"


def test_aim_tracks_near_fish_instead_of_larger_distant_shadow(monkeypatch):
    import numpy as np

    runner = make_runner(monkeypatch, held=True)
    frame = np.zeros((807, 1382, 3), dtype=np.uint8)
    old = SimpleNamespace(cx=971, cy=399)
    nearby = SimpleNamespace(cx=975, cy=403, area=720)
    large = SimpleNamespace(cx=700, cy=240, area=2600)
    detect = Mock(return_value=[large, nearby])
    monkeypatch.setattr(live, "find_shadows", detect)
    assert runner.fish_of(frame, old) is nearby
    detect.assert_called_once_with(frame, min_area=live.FISH_TRACK_MIN_AREA)
    detect.return_value = [large]
    assert runner.fish_of(frame, old) is None


def test_aim_without_fresh_frame_does_not_move_mouse(monkeypatch):
    runner = make_runner(monkeypatch, held=True)
    runner.phase, runner.casts = "aiming", 0
    runner.aim_frame_shape = (807, 1382)
    runner.aim_sample_at = runner.aim_seen_at = 0.
    monkeypatch.setattr(live, "time", SimpleNamespace(monotonic=lambda: 5., sleep=lambda _: None))
    runner.grab = lambda: None
    move = Mock()
    monkeypatch.setattr(live, "move_mouse", move)
    runner._tick()
    move.assert_not_called()
    assert runner.phase == "idle" and runner.casts == 0 and not runner.held

def test_preview_corrects_aim_but_does_not_release_before_five_seconds(monkeypatch):
    import numpy as np

    runner = make_runner(monkeypatch, held=True)
    runner.phase, runner.phase_at, runner.casts = "aiming", 0., 0
    runner.aim_sample_at = runner.aim_seen_at = 0.
    runner.aim_fish = SimpleNamespace(cx=800., cy=300.)
    frame = np.zeros((807, 1382, 3), dtype=np.uint8)
    runner.aim_frame_shape = frame.shape[:2]
    runner.grab = lambda: frame
    runner.fish_of = lambda frame, previous: SimpleNamespace(cx=810., cy=315.)
    monkeypatch.setattr(live, "find_ring", lambda *args, **kwargs: SimpleNamespace(cx=700., cy=280.))
    monkeypatch.setattr(live, "time", SimpleNamespace(monotonic=lambda: 4.99, sleep=lambda _: None))
    move = Mock()
    monkeypatch.setattr(live, "move_mouse", move)

    runner._tick()

    move.assert_called_once()
    runner.device.execute.assert_not_called()
    assert runner.held and runner.phase == "aiming" and runner.casts == 0
    assert runner.aim_fish.cx == 810.


def test_preview_does_not_aim_at_stale_target_after_five_seconds(monkeypatch):
    import numpy as np

    runner = make_runner(monkeypatch, held=True)
    runner.phase, runner.phase_at, runner.casts = "aiming", 0., 0
    runner.aim_sample_at = runner.aim_seen_at = 0.
    runner.aim_fish = SimpleNamespace(cx=800., cy=300.)
    frame = np.zeros((807, 1382, 3), dtype=np.uint8)
    runner.aim_frame_shape = frame.shape[:2]
    runner.grab = lambda: frame
    runner.fish_of = lambda frame, previous: None
    monkeypatch.setattr(live, "time", SimpleNamespace(monotonic=lambda: 5., sleep=lambda _: None))
    move = Mock()
    monkeypatch.setattr(live, "move_mouse", move)

    runner._tick()

    move.assert_not_called()
    assert runner.phase == "idle" and not runner.held and runner.casts == 0

def test_fishing_file_log_distinguishes_input_and_game_feedback(tmp_path):
    from utils.core.fishing_log import append_fishing_event, format_fishing_event

    path = tmp_path / "钓鱼日志.txt"
    inputs = {"at": "11:22:33", "ev": "move", "reason": "已发送：鼠标向右 20"}
    feedback = {"at": "11:22:34", "ev": "game_state", "reason": "浮标已入水，等待咬钩"}
    append_fishing_event(path, inputs)
    append_fishing_event(path, feedback)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines == ["11:22:33 [输入] 已发送：鼠标向右 20", "11:22:34 [游戏反馈] 浮标已入水，等待咬钩"]
    assert format_fishing_event(inputs)[1] == "已派发"
    assert format_fishing_event(feedback)[1] == "已确认"
