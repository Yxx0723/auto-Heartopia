"""Relative camera movement through Windows Raw Input, avoiding recenter noise."""
import ctypes
from ctypes import wintypes
import threading

import win32api
import win32con
import win32gui


class Header(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('size', wintypes.DWORD),
                ('device', wintypes.HANDLE), ('param', wintypes.WPARAM)]


class MouseData(ctypes.Structure):
    _fields_ = [('flags', wintypes.USHORT), ('buttons', wintypes.DWORD),
                ('raw_buttons', wintypes.ULONG), ('dx', wintypes.LONG),
                ('dy', wintypes.LONG), ('extra', wintypes.ULONG)]


class Device(ctypes.Structure):
    _fields_ = [('page', wintypes.USHORT), ('usage', wintypes.USHORT),
                ('flags', wintypes.DWORD), ('target', wintypes.HWND)]


class KeyboardData(ctypes.Structure):
    _fields_ = [('scan', wintypes.USHORT), ('flags', wintypes.USHORT),
                ('reserved', wintypes.USHORT), ('vk', wintypes.USHORT),
                ('message', wintypes.UINT), ('extra', wintypes.ULONG)]


class RawMouse:
    def __init__(self, callback, on_button=None, on_scroll=None, on_key=None, on_absolute=None):
        self.callback = callback
        self.on_button, self.on_scroll, self.on_key = on_button, on_scroll, on_key
        self.on_absolute = on_absolute
        self.ready = threading.Event()
        self.hwnd = 0
        self.error = None
        self.thread = None

    def dispatch(self, buffer):
        """Decode one native packet without polling or sending input."""
        offset = ctypes.sizeof(Header)
        if len(buffer) < offset:
            raise ValueError('输入数据头不完整')
        header = Header.from_buffer_copy(buffer)
        if header.type == 0:
            if len(buffer) < offset + ctypes.sizeof(MouseData):
                raise ValueError('鼠标输入数据不完整')
            data = MouseData.from_buffer_copy(buffer, offset)
            if data.flags & 1:
                if self.on_absolute:
                    self.on_absolute()
            elif data.dx or data.dy:
                self.callback(data.dx, data.dy)
            buttons = data.buttons & 0xffff
            if self.on_button:
                for index, name in enumerate(('left', 'right', 'middle', 'x1', 'x2')):
                    if buttons & (1 << (index * 2)):
                        self.on_button(name, True)
                    if buttons & (2 << (index * 2)):
                        self.on_button(name, False)
            if self.on_scroll and buttons & 0xc00:
                delta = ctypes.c_short(data.buttons >> 16).value
                self.on_scroll(delta if buttons & 0x800 else 0, delta if buttons & 0x400 else 0)
        elif header.type == 1 and self.on_key:
            if len(buffer) < offset + ctypes.sizeof(KeyboardData):
                raise ValueError('键盘输入数据不完整')
            data = KeyboardData.from_buffer_copy(buffer, offset)
            if data.vk != 255 and data.scan != 255:
                self.on_key(data.vk, data.scan, bool(data.flags & 2), not bool(data.flags & 1))

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError('鼠标录制监听初始化超时')
        if self.error:
            raise RuntimeError(f'鼠标录制监听初始化失败：{self.error}')

    def _run(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.RegisterRawInputDevices.argtypes = [ctypes.POINTER(Device), wintypes.UINT, wintypes.UINT]
        user.RegisterRawInputDevices.restype = wintypes.BOOL
        user.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                        ctypes.POINTER(wintypes.UINT), wintypes.UINT]
        user.GetRawInputData.restype = wintypes.UINT
        cls_name = 'MyRailRawMouse' + str(id(self))
        instance = win32api.GetModuleHandle(None)

        def procedure(hwnd, message, wp, lp):
            if message == 0x00ff:  # WM_INPUT
                try:
                    size = wintypes.UINT()
                    user.GetRawInputData(lp, 0x10000003, None, ctypes.byref(size), ctypes.sizeof(Header))
                    buffer = ctypes.create_string_buffer(size.value)
                    got = user.GetRawInputData(lp, 0x10000003, buffer, ctypes.byref(size), ctypes.sizeof(Header))
                    if got == 0xffffffff:
                        raise ctypes.WinError(ctypes.get_last_error())
                    self.dispatch(buffer.raw[:got])
                except Exception as exc:
                    self.error = exc
            elif message == win32con.WM_CLOSE:
                win32gui.DestroyWindow(hwnd)
                return 0
            elif message == win32con.WM_DESTROY:
                for usage in (2, 6) if self.on_key else (2,):
                    device = Device(1, usage, 1, None)  # RIDEV_REMOVE
                    user.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
                win32gui.PostQuitMessage(0)
                return 0
            return win32gui.DefWindowProc(hwnd, message, wp, lp)

        try:
            cls = win32gui.WNDCLASS()
            cls.hInstance, cls.lpszClassName, cls.lpfnWndProc = instance, cls_name, procedure
            win32gui.RegisterClass(cls)
            self.hwnd = win32gui.CreateWindow(cls_name, '', 0, 0, 0, 0, 0, 0, 0, instance, None)
            for usage in (2, 6) if self.on_key else (2,):
                device = Device(1, usage, 0x100, self.hwnd)  # RIDEV_INPUTSINK, passive only.
                if not user.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
                    raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set()
            win32gui.PumpMessages()
        except Exception as exc:
            self.error = exc
            self.ready.set()
        finally:
            if self.hwnd and win32gui.IsWindow(self.hwnd):
                win32gui.DestroyWindow(self.hwnd)
            try:
                win32gui.UnregisterClass(cls_name, instance)
            except Exception:
                pass

    def close(self):
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        if self.thread:
            self.thread.join(3)
