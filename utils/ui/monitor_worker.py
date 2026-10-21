"""Single background owner of coordinate reads; Tk consumes snapshots."""
import queue
import threading
from utils.core import thresholds as policy


class MonitorWorker:
    """One owner for the reader; Tk consumes snapshots without blocking on it."""
    def __init__(self, monitor):
        self.monitor = monitor
        self.results = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.reconnecting = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='coordinate-reader', daemon=True)
        self.thread.start()

    def set_paused(self, paused):
        self.paused.set() if paused else self.paused.clear()

    def reconnect(self):
        self.reconnecting.set()

    def take_latest(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def poll_once(self):
        if self.reconnecting.is_set():
            self.reconnecting.clear()
            self.monitor.reconnect()
        if self.paused.is_set():
            return
        status = self.monitor.status()
        try:
            self.results.put_nowait(status)
        except queue.Full:
            self.take_latest()
            self.results.put_nowait(status)

    def run(self):
        try:
            while not self.stop_event.is_set():
                self.poll_once()
                self.stop_event.wait(policy.COORD_MONITOR_REFRESH_SECONDS)
        finally:
            self.monitor.close()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=policy.COORD_MONITOR_RETRY_SECONDS)
        else:
            self.monitor.close()
