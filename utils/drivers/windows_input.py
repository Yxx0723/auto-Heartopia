"""Checked Windows SendInput calls; construction never sends device input."""
import ctypes
from ctypes import wintypes

import win32api


def enable_dpi_awareness():
    user = ctypes.WinDLL('user32', use_last_error=True)
    # Set before creating our windows; ignore access denied when already set.
    user.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


class MouseInput(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class HardwareInput(ctypes.Structure):
    _fields_ = [('message', wintypes.DWORD), ('low', wintypes.WORD), ('high', wintypes.WORD)]


class InputUnion(ctypes.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyboardInput), ('hi', HardwareInput)]


class Input(ctypes.Structure):
    _anonymous_ = ('payload',)
    _fields_ = [('type', wintypes.DWORD), ('payload', InputUnion)]


class WindowsInput:
    def __init__(self, send=None):
        if send is None:
            send = ctypes.WinDLL('user32', use_last_error=True).SendInput
            send.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
            send.restype = wintypes.UINT
        self.send = send

    def _send(self, event):
        ctypes.set_last_error(0)
        if self.send(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            code = ctypes.get_last_error()
            raise OSError(code, 'Windows拒绝了输入，请检查游戏和程序是否以相同权限运行')

    def key(self, vk, scan, extended, down):
        # Scancodes retain the physical key under a different keyboard layout.
        flags = (0x8 if scan else 0) | (0x1 if extended else 0) | (0 if down else 0x2)
        self._send(Input(type=1, ki=KeyboardInput(0 if scan else vk, scan, flags, 0, 0)))

    def unicode(self, unit, down):
        if type(unit) is not int or not 0 <= unit <= 0xffff:
            raise ValueError('文字输入编码无效')
        self._send(Input(type=1, ki=KeyboardInput(0, unit, 0x4 | (0 if down else 0x2), 0, 0)))

    def mouse(self, flags, dx=0, dy=0, data=0):
        self._send(Input(type=0, mi=MouseInput(dx, dy, data & 0xffffffff, flags, 0, 0)))

    def position(self, x, y):
        left, top, width, height = (win32api.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        nx = round((x - left) * 65535 / max(1, width - 1))
        ny = round((y - top) * 65535 / max(1, height - 1))
        self.mouse(0x8000 | 0x4000 | 1, max(0, min(65535, nx)), max(0, min(65535, ny)))
