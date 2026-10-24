"""Bind point playback to the monitored PID, never to the monitor's similar title."""
import win32gui
import win32process
import win32con
import pywintypes

from utils.drivers.town_window import TownWindow


class PointGameWindow(TownWindow):
    def __init__(self, pid):
        super().__init__('心动小镇')
        self.pid = pid
        candidates = []

        def collect(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                left, top, right, bottom = win32gui.GetClientRect(hwnd)
                if right - left >= 64 and bottom - top >= 64:
                    candidates.append(((right - left) * (bottom - top), hwnd))

        win32gui.EnumWindows(collect, None)
        if not candidates:
            raise ValueError('没有找到当前游戏进程的窗口，请进入游戏后重试。')
        self.hwnd = max(candidates)[1]

    def foreground(self):
        return bool(super().foreground() and win32process.GetWindowThreadProcessId(self.hwnd)[1] == self.pid)

    def bind_foreground(self):
        return self.foreground()

    def activate(self):
        """Activate only this PID's bound window after the user starts a task."""
        if win32process.GetWindowThreadProcessId(self.hwnd)[1] != self.pid:
            return False
        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(self.hwnd)
        except pywintypes.error:
            return False
        return self.foreground()
