"""Lazy foreground binding and input primitives for the user's town game."""
from PIL import ImageGrab
import win32api
import win32con
import win32gui

from utils.core.input_keys import KEY_VK, EXTENDED_KEYS, ordered_keys
from utils.drivers.windows_input import WindowsInput, enable_dpi_awareness


class TownWindow:
    def __init__(self, title='心动小镇'):
        enable_dpi_awareness()
        self.title = title
        self.hwnd = 0

    def bind_foreground(self):
        hwnd = win32gui.GetForegroundWindow()
        actual = win32gui.GetWindowText(hwnd).strip()
        # Exclude a browser page whose title happens to mention the game.
        if self.title not in actual or any(s in actual for s in ('Google Chrome', 'Microsoft Edge', 'Mozilla Firefox', 'MyRail')):
            return False
        rect = self._rect(hwnd)
        if rect[2] - rect[0] < 64 or rect[3] - rect[1] < 64:
            return False
        if self.hwnd and self.hwnd != hwnd:
            if not win32gui.IsWindow(self.hwnd):
                self.hwnd = 0
            else:
                return False
        self.hwnd = hwnd
        return True

    def foreground(self):
        return bool(self.hwnd and win32gui.IsWindow(self.hwnd) and win32gui.GetForegroundWindow() == self.hwnd)

    @staticmethod
    def _rect(hwnd):
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        x, y = win32gui.ClientToScreen(hwnd, (left, top))
        return x, y, x + right - left, y + bottom - top

    def get_rect(self):
        return self._rect(self.hwnd)

    def size(self):
        left, top, right, bottom = self.get_rect()
        return [right - left, bottom - top]

    def snapshot(self):
        return ImageGrab.grab(bbox=self.get_rect(), all_screens=True)

    def pointer(self):
        flags, _, (x, y) = win32gui.GetCursorInfo()
        left, top, _, _ = self.get_rect()
        return x - left, y - top, bool(flags & 1)


class TimelineInput:
    def __init__(self, window=None, native=None):
        self.keys = set()
        self.buttons = set()
        self.key_specs = {}
        self.window = window
        self.native = native or WindowsInput()

    def _key(self, key, down):
        spec = self.key_specs.get(key, {})
        vk = spec.get('vk', KEY_VK[key])
        scan = spec.get('scan', win32api.MapVirtualKey(vk, 0))
        if key == 'pause':
            scan = 0  # Pause has an E1 sequence, not a normal single scancode.
        self.native.key(vk, scan & 0xff, spec.get('extended', key in EXTENDED_KEYS), down)

    def _button(self, button, down):
        flags = {('left', True): win32con.MOUSEEVENTF_LEFTDOWN,
                 ('left', False): win32con.MOUSEEVENTF_LEFTUP,
                 ('right', True): win32con.MOUSEEVENTF_RIGHTDOWN,
                 ('right', False): win32con.MOUSEEVENTF_RIGHTUP,
                 ('middle', True): win32con.MOUSEEVENTF_MIDDLEDOWN,
                 ('middle', False): win32con.MOUSEEVENTF_MIDDLEUP}
        if button in ('x1', 'x2'):
            self.native.mouse(0x80 if down else 0x100, data=1 if button == 'x1' else 2)
        else:
            self.native.mouse(flags[button, down])

    def _position(self, x, y):
        if self.window is None:
            raise RuntimeError('鼠标位置回放缺少游戏窗口')
        left, top, right, bottom = self.window.get_rect()
        self.native.position(left + max(0, min(right - left - 1, x)),
                             top + max(0, min(bottom - top - 1, y)))

    def execute(self, event):
        kind = event['type']
        if kind in ('key_down', 'key_up'):
            key, down = event['key'], kind == 'key_down'
            if down:
                self.key_specs[key] = {k: event[k] for k in ('vk', 'scan', 'extended') if k in event}
                self.keys.add(key)  # Track before injecting so failures can release it.
            self._key(key, down)
            if not down:
                self.keys.discard(key)
        elif kind in ('mouse_down', 'mouse_up'):
            button, down = event['button'], kind == 'mouse_down'
            if 'x' in event:
                self._position(event['x'], event['y'])
            if down:
                self.buttons.add(button)
            self._button(button, down)
            if not down:
                self.buttons.discard(button)
        elif kind == 'mouse_move':
            if event.get('cursor') and not self.buttons & {'right', 'middle'}:
                self._position(event['x'], event['y'])
            else:
                self.native.mouse(win32con.MOUSEEVENTF_MOVE | 0x2000, event['dx'], event['dy'])
        elif kind == 'cursor_move':
            self._position(event['x'], event['y'])
        elif kind == 'scroll':
            factor = 1 if event.get('unit') == 'delta' else 120
            if event['dy']:
                self.native.mouse(win32con.MOUSEEVENTF_WHEEL, data=event['dy'] * factor)
            if event['dx']:
                self.native.mouse(0x1000, data=event['dx'] * factor)

    def type_text(self, text, check, wait):
        if not isinstance(text, str) or not 1 <= len(text) <= 32 or any(ord(c) < 32 for c in text):
            raise ValueError('搜索文本无效')
        encoded = text.encode('utf-16-le')
        for offset in range(0, len(encoded), 2):
            check()
            unit = int.from_bytes(encoded[offset:offset+2], 'little')
            try:
                self.native.unicode(unit, True)
            finally:
                self.native.unicode(unit, False)
            wait(.03)

    def release_all(self):
        errors = []
        for key in list(self.keys):
            try:
                self._key(key, False)
                self.keys.discard(key)
            except Exception as exc:
                errors.append(exc)
        for button in list(self.buttons):
            try:
                self._button(button, False)
                self.buttons.discard(button)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]

    def restore(self, keys, buttons):
        for key in ordered_keys(keys):
            self.execute({'type': 'key_down', 'key': key, **self.key_specs.get(key, {})})
        for button in buttons:
            self.execute({'type': 'mouse_down', 'button': button})
