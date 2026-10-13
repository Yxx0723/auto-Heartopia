"""Only the monitored game PID may receive point playback input."""
import pytest

from utils.drivers.point_game_window import PointGameWindow


def fake_windows(monkeypatch, game=True):
    import utils.drivers.point_game_window as module
    monkeypatch.setattr('utils.drivers.town_window.enable_dpi_awareness', lambda: None)
    state = {'foreground': 100, 'pid': 77}
    handles = [100, 200, 300] if game else [100]
    monkeypatch.setattr(module.win32gui, 'EnumWindows', lambda callback, extra: [callback(h, extra) for h in handles])
    monkeypatch.setattr(module.win32gui, 'IsWindowVisible', lambda h: True)
    monkeypatch.setattr(module.win32gui, 'IsWindow', lambda h: h in handles)
    monkeypatch.setattr(module.win32gui, 'GetClientRect', lambda h: (0, 0, 1600, 900) if h != 300 else (0, 0, 20, 20))
    monkeypatch.setattr(module.win32gui, 'GetForegroundWindow', lambda: state['foreground'])
    monkeypatch.setattr(module.win32process, 'GetWindowThreadProcessId', lambda h: (1, state['pid'] if h in (200, 300) else 1234))
    return state


def test_f6_on_monitor_or_other_window_cannot_bind_the_game(monkeypatch):
    state = fake_windows(monkeypatch)
    window = PointGameWindow(77)
    assert window.hwnd == 200
    assert not window.bind_foreground()
    assert not window.foreground()
    state['foreground'] = 200
    assert window.bind_foreground() and window.foreground()
    state['pid'] = 88
    assert not window.bind_foreground() and not window.foreground()


def test_no_window_for_monitored_pid_is_an_error(monkeypatch):
    fake_windows(monkeypatch, game=False)
    with pytest.raises(ValueError, match='窗口'):
        PointGameWindow(77)
