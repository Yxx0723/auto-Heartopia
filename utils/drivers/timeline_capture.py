"""Passive input capture. Hotkeys start before the game window is bound."""
import threading
import time
import queue

from utils.core.input_keys import CONTROL_KEYS, key_from_vk, ordered_keys, key_name
from utils.drivers.raw_mouse import RawMouse




class Capture:
    def __init__(self, timeline, window, feedback, stop_requested=lambda: False,
                 remember_start=lambda: None, clock=time.perf_counter, on_tick=lambda: None):
        self.timeline, self.window, self.feedback = timeline, window, feedback
        self.stop_requested, self.remember_start, self.clock = stop_requested, remember_start, clock
        self.on_tick = on_tick
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.controls = set()
        self.cancelled = False
        self.started = False
        self.size = None
        self.error = None
        self.held_keys = {}
        self.held_buttons = set()
        self.notifications = queue.SimpleQueue()
        self.snapshot_pending = False

    def _notify(self, state, message):
        self.notifications.put((state, message))

    def flush_feedback(self):
        while not self.notifications.empty():
            state, message = self.notifications.get_nowait()
            self.feedback.update(state, message, self.timeline, self.window)

    def raw_key(self, vk, scan, extended, down):
        name = key_from_vk(vk, scan, extended)
        identity = {'vk': vk, 'extended': extended}
        if scan:
            identity['scan'] = scan
        self._key_event(name, down, identity)

    def press(self, key):
        self._key_event(key_name(key), True, {})

    def release(self, key):
        self._key_event(key_name(key), False, {})

    def _key_event(self, name, down, identity):
        try:
            with self.lock:
                if not down:
                    self.controls.discard(name)
                    self.held_keys.pop(name, None)
                    self.timeline.key(name, False, self.clock())
                    return
                if name in CONTROL_KEYS:
                    if name in self.controls:
                        return
                    self.controls.add(name)
                    if name == 'f10':
                        self.cancelled = True
                        self.timeline.pause(self.clock())
                        self.done.set()
                    elif name == 'f9':
                        self.timeline.pause(self.clock())
                        self.done.set()
                    elif self.timeline.active:
                        self.timeline.pause(self.clock())
                        self._notify('paused', '录制已暂停 · F6继续 · F9保存')
                    elif not self.window.bind_foreground():
                        self._notify('paused' if self.started else 'ready',
                                     f'请切回{self.window.title}游戏窗口，再按F6')
                    elif self.started and self.window.size() != self.size:
                        self._notify('paused', '窗口尺寸已改变，请恢复录制时的尺寸再按F6')
                    else:
                        if not self.started:
                            self.size = self.window.size()
                            self.snapshot_pending = True
                            self.started = True
                        now = self.clock()
                        self.timeline.resume(now)
                        for held in ordered_keys(self.held_keys):
                            self.timeline.key(held, True, now, **self.held_keys[held])
                        for held in self.held_buttons:
                            self.timeline.button(held, True, now)
                        self._notify('recording', '正在录制 · F6暂停 · F9保存 · F10取消')
                else:
                    self.held_keys[name] = identity
                    if self.timeline.active and self.window.foreground():
                        self.timeline.key(name, True, self.clock(), **identity)
        except Exception as exc:
            self.error = exc
            self.done.set()

    def motion(self, dx, dy):
        with self.lock:
            if self.timeline.active and self.window.foreground():
                x, y, visible = self.window.pointer()
                self.timeline.move(dx, dy, self.clock(), position=(x, y) if visible else None)

    def raw_button(self, button, pressed):
        with self.lock:
            camera_drag = bool(self.held_buttons & {'right', 'middle'})
            (self.held_buttons.add if pressed else self.held_buttons.discard)(button)
            if self.timeline.active and self.window.foreground():
                x, y, visible = self.window.pointer()
                self.timeline.button(button, pressed, self.clock(),
                                     position=(x, y) if visible and not camera_drag else None)

    def absolute_motion(self):
        with self.lock:
            if self.timeline.active and self.window.foreground():
                x, y, _ = self.window.pointer()
                self.timeline.position(x, y, self.clock())

    def raw_scroll(self, dx, dy):
        with self.lock:
            if self.timeline.active and self.window.foreground():
                self.timeline.scroll(dx, dy, self.clock(), raw=True)

    def click(self, x, y, button, pressed, injected=False):
        with self.lock:
            if self.timeline.active and self.window.foreground():
                self.timeline.button(button.name, pressed, self.clock())

    def scroll(self, x, y, dx, dy, injected=False):
        with self.lock:
            if self.timeline.active and self.window.foreground():
                self.timeline.scroll(dx, dy, self.clock())

    def run(self):
        raw = RawMouse(self.motion, on_button=self.raw_button, on_scroll=self.raw_scroll,
                       on_key=self.raw_key, on_absolute=self.absolute_motion)
        try:
            self._notify('ready', f'录制器已就绪 · 切到{self.window.title}，按F6开始')
            raw.start()
            self.flush_feedback()
            while not self.done.wait(0.01):
                if self.stop_requested():
                    self.cancelled = True
                    break
                if raw.error or not raw.thread.is_alive():
                    raise RuntimeError(f'输入监听异常：{raw.error or "键鼠监听已退出"}')
                with self.lock:
                    if self.timeline.active and (not self.window.foreground() or self.window.size() != self.size):
                        self.timeline.pause(self.clock())
                        self._notify('paused', '游戏失去焦点或尺寸改变，已暂停 · 切回游戏按F6继续')
                self.flush_feedback()
                with self.lock:
                    self.on_tick()
                if self.snapshot_pending:
                    self.snapshot_pending = False
                    self.remember_start()
                self.feedback.refresh(self.timeline, self.window)
            if self.snapshot_pending:
                self.remember_start()
            if self.error:
                raise self.error
        finally:
            with self.lock:
                self.timeline.pause(self.clock())
            raw.close()
            self.flush_feedback()
        return not self.cancelled
