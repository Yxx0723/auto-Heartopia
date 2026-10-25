"""Independent read-only scene snapshots; Tk receives immutable queue values."""
import queue
import threading
import time
from utils.core import thresholds as policy
from utils.core.scene_read import issue_summary
from utils.core.scene_display_cache import SceneDisplayCache
from utils.drivers.mono_scene import MemorySceneMap


class SceneMapWorker:
    def __init__(self, profile, factory=MemorySceneMap, clock=time.monotonic):
        self.profile, self.factory = profile, factory
        self.clock = clock
        self.latest_scene, self.latest_at = None, None
        self.results, self.lock = queue.Queue(maxsize=1), threading.Lock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.pid, self.generation, self.reader_pid = None, 0, None
        self.reader, self.thread = None, None
        self.history = SceneDisplayCache()

    def start(self):
        self.thread = threading.Thread(target=self.run, name='scene-map-reader', daemon=True)
        self.thread.start()

    def take_latest(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def set_pid(self, pid):
        with self.lock:
            if self.pid == pid:
                return False
            self.pid, self.generation = pid, self.generation + 1
            self.latest_scene, self.latest_at = None, None
            self.history = SceneDisplayCache()
            self.take_latest()
            self.wake.set()
            return True

    def refresh(self):
        self.wake.set()

    def clear_history(self):
        with self.lock:
            self.history = SceneDisplayCache()
            self.wake.set()

    def navigation_snapshot(self, pid):
        """Independent of the UI queue and category/visibility switches."""
        with self.lock:
            if pid != self.pid or self.stop.is_set() or self.latest_scene is None \
                    or self.latest_scene.get('state') != 'ready' \
                    or self.latest_scene.get('world_state', 'ready') != 'ready' \
                    or self.clock() - self.latest_at > policy.COORD_NAV_SCENE_MAX_AGE:
                return None
            return self.latest_scene

    def close_reader(self):
        if self.reader is not None:
            self.reader.close()
        self.reader, self.reader_pid = None, None

    def poll_once(self):
        with self.lock:
            pid, generation = self.pid, self.generation
            history = self.history
        if pid != self.reader_pid:
            self.close_reader()
        if pid is None:
            return False
        try:
            if self.reader is None:
                self.reader = self.factory(pid, self.profile)
                self.reader_pid = pid
            result = self.reader.snapshot()
        except (OSError, ValueError) as exc:
            self.close_reader()
            result = {'state': 'error', 'objects': [], 'colliders': [],
                      'message': '场景正在更新，稍后自动重试', 'diagnostic': str(exc),
                      'issue': issue_summary(exc)}
        now = self.clock()
        display = history.update(result, now)
        with self.lock:
            if generation == self.generation and not self.stop.is_set():
                self.latest_scene, self.latest_at = result, now
                self.take_latest()
                # A clear request during this read must not republish old history.
                self.results.put_nowait(display if self.history is history else result)
                return result.get('state') == 'ready'
        return False

    def run(self):
        try:
            while not self.stop.is_set():
                self.wake.clear()
                started = self.clock()
                ready = self.poll_once()
                elapsed = max(0., self.clock()-started)
                delay = max(policy.COORD_SCENE_MIN_IDLE_SECONDS,
                            policy.COORD_SCENE_REFRESH_SECONDS-elapsed) if ready else policy.COORD_MONITOR_RETRY_SECONDS
                self.wake.wait(delay)
        finally:
            self.close_reader()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=policy.COORD_MONITOR_RETRY_SECONDS)
        else:
            self.close_reader()
