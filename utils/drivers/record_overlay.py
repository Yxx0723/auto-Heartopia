"""Click-through, non-activating status strip. Never takes keyboard focus."""
import queue
import threading

import win32api
import win32con
import win32gui


class StatusOverlay:
    def __init__(self):
        self.messages = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def update(self, message, rect=None):
        self.messages.put((message, rect))

    def close(self):
        self.messages.put(None)
        self.thread.join(2)

    def _run(self):
        hwnd = font = None
        try:
            style = 0x08000000 | 0x20 | 0x80 | 0x80000 | 0x8
            hwnd = win32gui.CreateWindowEx(style, 'STATIC', '', win32con.WS_POPUP | 0x200,
                                           20, 20, 570, 68, 0, 0, win32api.GetModuleHandle(None), None)
            spec = win32gui.LOGFONT()
            spec.lfFaceName = 'Microsoft YaHei'
            spec.lfHeight = -18
            font = win32gui.CreateFontIndirect(spec)
            win32gui.SendMessage(hwnd, win32con.WM_SETFONT, font, True)
            win32gui.SetLayeredWindowAttributes(hwnd, 0, 235, 2)
            while True:
                win32gui.PumpWaitingMessages()
                try:
                    item = self.messages.get(timeout=0.05)
                except queue.Empty:
                    continue
                if item is None:
                    break
                text, rect = item
                x, y = (rect[0] + 16, rect[1] + 16) if rect else (20, 20)
                win32gui.SetWindowText(hwnd, text)
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, 570, 68,
                                      win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
        except Exception as exc:
            print(f'浮动提示不可用，请看控制面板状态：{exc}', flush=True)
        finally:
            if hwnd and win32gui.IsWindow(hwnd):
                win32gui.DestroyWindow(hwnd)
            if font:
                win32gui.DeleteObject(font)
