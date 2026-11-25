"""Independent release for the left mouse button during blocking game reads."""
from threading import Event, Lock, Thread
import time

from utils.core import thresholds as policy


class FishingInput:
    def __init__(self, device, check, clock=time.monotonic):
        self.device,self.check,self.clock = device,check,clock
        self.lock = Lock()
        self.finished = Event()
        self.held = False
        self.deadline = 0.
        self.failure = None
        self.thread = Thread(target=self._monitor,name='fishing-input-watch',daemon=True)

    def start(self):
        self.thread.start()

    def guard(self):
        if self.failure is not None:
            raise InterruptedError(self.failure)
        self.check()

    def press(self):
        with self.lock:
            self.guard()
            self.deadline = self.clock()+policy.FISHING_INPUT_LEASE
            if not self.held:
                self.held = True
                self.device.execute({'type':'mouse_down','button':'left'})

    def release(self, move=None):
        with self.lock:
            try:
                if move is not None:
                    self.guard()
                    if not self.held or self.clock() >= self.deadline:
                        raise InterruptedError('瞄准输入许可已过期')
                    move()
            finally:
                self._release()

    def _release(self):
        if self.held:
            self.device.execute({'type':'mouse_up','button':'left'})
            self.held = False

    def _monitor(self):
        while not self.finished.wait(policy.FISHING_INPUT_POLL):
            with self.lock:
                if not self.held:
                    continue
                try:
                    self.check()
                    if self.clock() >= self.deadline:
                        raise InterruptedError('观测超过输入许可时限，已自动松开左键')
                except Exception as exc:
                    self.failure = str(exc)
                    try:
                        self._release()
                    except Exception:
                        # Retry independently on the next watchdog cycle.
                        continue

    def close(self):
        try:
            self.release()
        finally:
            self.finished.set()
            if self.thread.is_alive():
                self.thread.join(timeout=.2)
