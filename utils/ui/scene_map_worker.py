"""Independent read-only scene snapshots; Tk receives immutable queue values."""
import queue
import threading
import time
from utils.core import thresholds as policy
from utils.core.scene_display_cache import SceneDisplayCache
from utils.drivers.mono_scene import MemorySceneMap
from utils.drivers.special_resource_locations import SpecialResourceLocations
from utils.drivers.fixed_resource_cache import FixedResourceCache
from utils.ui.playback_diagnostics import PlaybackDiagnostics


class SceneMapWorker:
    def __init__(self, profile, factory=MemorySceneMap, clock=time.monotonic, special_locations_path=None,
                 fixed_resources_path=None, diagnostics_path=None):
        self.profile, self.factory = profile, factory
        self.clock = clock
        self.latest_scene, self.latest_at = None, None
        self.results, self.lock = queue.Queue(maxsize=1), threading.Lock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.pid, self.generation, self.reader_pid = None, 0, None
        self.reader, self.thread = None, None
        self.failures, self.last_display, self.last_display_at = 0, None, None
        self.diagnostics = PlaybackDiagnostics(diagnostics_path) if diagnostics_path else None
        self.diagnostic_key, self.diagnostic_at = None, float('-inf')
        self.history = SceneDisplayCache()
        self.special_locations = SpecialResourceLocations(special_locations_path) if special_locations_path else None
        self.fixed_resources = FixedResourceCache(fixed_resources_path) if fixed_resources_path else None

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
            self.last_display, self.last_display_at, self.failures = None, None, 0
            self.history = SceneDisplayCache()
            self.take_latest()
            self.wake.set()
            return True

    def refresh(self):
        self.wake.set()

    def clear_history(self):
        with self.lock:
            self.history = SceneDisplayCache()
            self.last_display, self.last_display_at = None, None
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
            return
        try:
            if self.reader is None:
                self.reader = self.factory(pid, self.profile)
                self.reader_pid = pid
            try:
                result = self.reader.snapshot()
                if result.get('world_state') == 'unavailable':
                    result = self.reader.snapshot()
            except ValueError:
                result = self.reader.snapshot()  # One coherent fresh retry, never merge torn reads.
            self.failures = 0
            if self.special_locations is not None:
                result = self.special_locations.enrich(result)
        except (OSError, ValueError) as exc:
            self.failures += 1
            if isinstance(exc, OSError) or self.failures >= 3:
                self.close_reader()
            result = {'state': 'error', 'objects': [], 'colliders': [],
                      'message': '场景正在更新，稍后自动重试', 'diagnostic': str(exc)}
        now = self.clock()
        display = history.update(result, now)
        if self.fixed_resources is not None:
            display = self.fixed_resources.enrich(display)
        with self.lock:
            if generation == self.generation and not self.stop.is_set():
                self.latest_scene, self.latest_at = result, now
                if result.get('state') == 'ready' and result.get('world_state', 'ready') == 'ready':
                    self.last_display, self.last_display_at = (display if self.history is history else result), now
                elif (result.get('state') == 'error' or result.get('world_state') == 'unavailable') \
                        and self.last_display is not None \
                        and now-self.last_display_at <= 6.:
                    # Short-lived, explicitly stale display reference only.
                    # Animals and gather state disappear; navigation above
                    # always sees the failed fresh result, never this fallback.
                    old = self.last_display
                    objects = [{**o, 'map_cached': True} for o in old.get('objects', ())
                               if o.get('map_static') is True and o.get('category') in
                               ('tree', 'bush', 'shrub', 'blueberry', 'raspberry', 'ore', 'rock')]
                    objects = [{k:v for k,v in o.items() if k not in
                                ('resource_available', 'resource_cold_end_ms', 'resource_state_digest')}
                               for o in objects]
                    display = {**old, **result, 'state': 'updating', 'world_state': 'unavailable',
                               'display_stale': True, 'objects': objects,
                               'colliders': [{**c, 'map_cached': True} for c in old.get('colliders', ())
                                             if c.get('map_static') is True],
                               'global_special_resources': [], 'global_map_resources': [],
                               'global_special_state': 'unavailable', 'global_map_state': 'unavailable',
                               'message': '场景更新中，暂显固定参考；寻路等待读取恢复'}
                else:
                    self.last_display, self.last_display_at = None, None
                diagnostic = result.get('diagnostic') or result.get('world_diagnostic')
                key = (result.get('state'), result.get('world_state'), diagnostic)
                if self.diagnostics and (key != self.diagnostic_key or now-self.diagnostic_at >= 10.):
                    self.diagnostics.write({'state': result.get('state'),
                        'message': f'world={result.get("world_state")} 失败轮次={self.failures} '
                                   f'{diagnostic or "读取正常"}'})
                    self.diagnostic_key, self.diagnostic_at = key, now
                self.take_latest()
                # A clear request during this read must not republish old history.
                self.results.put_nowait(display if self.history is history else result)

    def run(self):
        try:
            while not self.stop.is_set():
                self.wake.clear()
                self.poll_once()
                self.wake.wait(policy.COORD_SCENE_REFRESH_SECONDS)
        finally:
            self.close_reader()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=policy.COORD_SCENE_REFRESH_SECONDS)
        else:
            self.close_reader()
        if self.diagnostics:
            self.diagnostics.close()
