"""One task may own game input, including concurrent startup races."""
from contextlib import contextmanager
import threading


class InputTaskGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.owner = None

    @contextmanager
    def claim(self, name):
        with self.lock:
            if self.owner is not None:
                raise ValueError('游戏输入正由'+self.owner+'占用，请先停止该任务')
            self.owner = name
        try:
            yield
        finally:
            with self.lock:
                self.owner = None
