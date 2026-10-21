"""Playback controls; never activates or searches for another window."""
import threading

from utils.core.input_keys import key_name


class PlaybackControls:
    def __init__(self, window, size, feedback, stop_requested=lambda: False):
        self.window, self.size, self.feedback = window, size, feedback
        self.stop_requested = stop_requested
        self.lock = threading.RLock()
        self.running = False
        self.stopped = False
        self.error = None
        self.held = set()
        self.listener = None

    def press(self, key):
        try:
            with self.lock:
                name = key_name(key)
                if name not in {'f6', 'f9', 'f10'} or name in self.held:
                    return
                self.held.add(name)
                if name in {'f9', 'f10'}:
                    self.stopped = True
                elif self.running:
                    self.running = False
                    self.feedback.update('paused', '回放已暂停 · F6继续 · F9停止', window=self.window)
                elif not self.window.bind_foreground():
                    self.feedback.update('playback_wait', f'请切回{self.window.title}，站回起点后按F6', window=self.window)
                elif self.window.size() != self.size:
                    self.feedback.update('playback_wait', f'窗口尺寸不一致，请恢复为{self.size[0]}×{self.size[1]}后按F6', window=self.window)
                else:
                    self.running = True
                    self.feedback.update('playing', '正在回放 · F6暂停 · F9停止', window=self.window)
        except Exception as exc:
            self.error = exc
            self.stopped = True

    def release(self, key):
        with self.lock:
            self.held.discard(key_name(key))

    def snapshot(self):
        with self.lock:
            if self.error:
                raise self.error
            if self.listener and not self.listener.is_alive():
                raise RuntimeError('回放热键监听已退出')
            return self.running, self.stopped or self.stop_requested()

    def pause(self):
        self.pause_with_reason('游戏失去焦点或尺寸改变，已暂停 · 切回游戏按F6继续')

    def pause_with_reason(self, message):
        with self.lock:
            self.running = False
            self.feedback.update('paused', message, window=self.window)

    def start(self):
        from pynput import keyboard
        self.listener = keyboard.Listener(on_press=self.press, on_release=self.release)
        self.listener.start()
        self.listener.wait()
        self.feedback.update('playback_wait', f'回放已就绪 · 站回起点、恢复镜头 · 切到{self.window.title}按F6', window=self.window)

    def close(self):
        if self.listener:
            self.listener.stop()
            self.listener.join(timeout=2)
