"""Independent read-only scene snapshots; Tk receives immutable queue values."""
import queue
import threading
import time
from utils.core import thresholds as policy
from utils.core.scene_read import issue_summary, navigation_quality_valid
from utils.ui.playback_diagnostics import PlaybackDiagnostics
import json
from utils.core.scene_display_cache import SceneDisplayCache
from utils.core.navigation_supply import SnapshotSupply
from utils.core.prepared_navigation import PreparedNavigationScene
from utils.drivers.mono_scene import MemorySceneMap


class SceneMapWorker:
    def __init__(self, profile, factory=MemorySceneMap, clock=time.monotonic, diagnostics_path=None):
        self.profile, self.factory = profile, factory
        self.clock = clock
        self.latest_scene, self.latest_at = None, None
        self.results, self.lock = queue.Queue(maxsize=1), threading.Lock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.pid, self.generation, self.reader_pid = None, 0, None
        self.reader, self.thread = None, None
        self.history = SceneDisplayCache()
        self.transient_failures = 0
        self.fresh_failures = 0
        self.retry_delay = policy.COORD_MONITOR_RETRY_SECONDS
        self.diagnostics = PlaybackDiagnostics(diagnostics_path) if diagnostics_path is not None else None
        self.last_diagnostic_at = None
        self.supply = SnapshotSupply()

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
            self.transient_failures = 0
            self.fresh_failures = 0
            self.retry_delay = policy.COORD_MONITOR_RETRY_SECONDS
            self.latest_scene, self.latest_at = None, None
            self.history = SceneDisplayCache()
            self.supply = SnapshotSupply()
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
            if pid == self.pid and not self.stop.is_set():
                self.supply.start(self.clock())
            if pid != self.pid or self.stop.is_set() or self.latest_scene is None \
                    or self.latest_scene.get('state') != 'ready' \
                    or self.latest_scene.get('world_state', 'ready') != 'ready' \
                    or self.clock() - self.latest_at > policy.COORD_NAV_SCENE_MAX_AGE \
                    or not navigation_quality_valid(self.latest_scene, self.clock()):
                return None
            return self.latest_scene

    def navigation_metrics(self, pid):
        with self.lock:
            return self.supply.metrics(self.clock()) if pid == self.pid else None

    def pause_navigation_metrics(self):
        with self.lock:
            self.supply.pause(self.clock())

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
        started, read_stats, transient, fresh_retry = self.clock(), {}, False, False
        try:
            if self.reader is None:
                self.reader = self.factory(pid, self.profile)
                self.reader_pid = pid
                if isinstance(self.reader, MemorySceneMap):
                    self.reader.cancelled = lambda: self.stop.is_set() or self.generation != generation
            result = self.reader.snapshot()
        except (OSError, ValueError) as exc:
            issue = issue_summary(exc)
            transient = issue['kind'] == 'mutation' and issue['retryable'] and issue['scope_verified']
            # A known navigation-only reference failure still discards the
            # reader and its snapshot. Reopen from scratch sooner; this is NOT
            # permission to retain data or promote the error to a mutation.
            fresh_retry = (isinstance(self.reader, MemorySceneMap)
                and getattr(self.reader, 'navigation_only', False) is True
                and issue['stage'] in ('managed_group_colliders', 'managed_collider_stream',
                    'managed_stream_collisions', 'managed_collision_array', 'managed_shape_data')
                and (issue['kind'] == 'invalid_reference'
                     or issue.get('code') in ('short_read', 'read_bounds')))
            if isinstance(self.reader, MemorySceneMap):
                read_stats = self.reader.last_read_stats
            if not transient:
                self.close_reader()
            result = {'state': 'error', 'objects': [], 'colliders': [],
                      'message': '场景正在更新，稍后自动重试', 'diagnostic': str(exc), 'issue': issue}
        read_finished = self.clock()
        geometry_preparation_seconds = 0.
        if result.get('navigation_only') is True and result.get('state') == 'ready' \
                and navigation_quality_valid(result, read_finished):
            preparation_started = self.clock()
            try:
                result = PreparedNavigationScene(result)
            except (ValueError, TypeError, KeyError, MemoryError):
                # Discard this entire publication, never reuse old geometry or
                # move forward with a partially prepared signature.
                result = {'state': 'error', 'objects': [], 'colliders': [],
                          'message': '导航几何准备失败，等待重新读取', 'navigation_only': True}
            geometry_preparation_seconds = max(0., self.clock()-preparation_started)
        if isinstance(self.reader, MemorySceneMap):
            read_stats = self.reader.last_read_stats
        # Dedicated guidance readers never feed a map display. Do not run the
        # display cache or its geometry processing before navigation publication.
        display = result if result.get('navigation_only') is True else history.update(result, read_finished)
        now = self.clock()
        with self.lock:
            if generation != self.generation or self.stop.is_set():
                return False
            self.transient_failures = min(self.transient_failures+1, 1000000) if transient else 0
            self.fresh_failures = min(self.fresh_failures+1, 1000000) if fresh_retry else 0
            recovery_failures = self.transient_failures if transient else self.fresh_failures
            self.retry_delay = (min(policy.COORD_MONITOR_RETRY_SECONDS,
                policy.COORD_SCENE_TRANSIENT_RETRY_SECONDS * 2**min(recovery_failures-1, 5))
                if transient or fresh_retry else policy.COORD_MONITOR_RETRY_SECONDS)
            self.latest_scene, self.latest_at = result, now
            self.supply.publish(result, now, max(0., now-started))
            supply_metrics = self.supply.metrics(now)
            navigation_usable = self.supply.deadline is not None
            self.take_latest()
            # A clear request during this read must not republish old history.
            self.results.put_nowait(display if self.history is history else result)
        if self.diagnostics and (result.get('issue') or not navigation_usable or self.last_diagnostic_at is None
                or now-self.last_diagnostic_at >= policy.COORD_SCENE_DIAGNOSTIC_SECONDS):
            self.last_diagnostic_at = now
            quality = result.get('quality')
            parts = quality.get('parts') if isinstance(quality, dict) else None
            parts = parts if isinstance(parts, dict) else {}
            self.diagnostics.write({'state': result.get('state'), 'message': json.dumps({
                'duration_seconds': max(0., now-started), 'recovery': read_stats,
                'publication_seconds': max(0., now-read_finished),
                'geometry_preparation_seconds': geometry_preparation_seconds,
                'supply': supply_metrics,
                'issue': result.get('issue'), 'transient_failures': self.transient_failures,
                'fresh_restart_failures': self.fresh_failures, 'fresh_restart': fresh_retry,
                'retry_delay': self.retry_delay,
                'navigation_eligible': quality.get('navigation_eligible') if isinstance(quality, dict) else None,
                'navigation_usable_now': navigation_usable,
                'navigation_only': result.get('navigation_only',False),
                'part_ages': {key:max(0.,now-value['observed_from']) for key,value in parts.items()
                              if isinstance(value,dict) and type(value.get('observed_from')) in (int,float)},
                'discovery_unknown': quality.get('discovery_unknown') if isinstance(quality, dict) else None,
                'managed_unknown': quality.get('managed_unknown') if isinstance(quality, dict) else None,
                'parts': {key: value.get('state') if isinstance(value, dict) else 'unavailable'
                          for key, value in parts.items()}}, ensure_ascii=False)})
        return result.get('state') == 'ready'

    def run(self):
        try:
            while not self.stop.is_set():
                self.wake.clear()
                started = self.clock()
                ready = self.poll_once()
                elapsed = max(0., self.clock()-started)
                delay = max(policy.COORD_SCENE_MIN_IDLE_SECONDS,
                            policy.COORD_SCENE_REFRESH_SECONDS-elapsed) if ready else self.retry_delay
                self.wake.wait(delay)
        finally:
            self.close_reader()
            if self.diagnostics:
                with self.lock:
                    metrics = self.supply.metrics(self.clock())
                self.diagnostics.write({'state': 'supply_summary', 'message': json.dumps(metrics)})
                self.diagnostics.close()

    def close(self):
        self.pause_navigation_metrics()
        self.stop.set()
        self.wake.set()
        interrupt = getattr(self.reader, 'interrupt', None)
        if callable(interrupt):
            interrupt()
        if self.thread:
            self.thread.join(timeout=policy.COORD_MONITOR_RETRY_SECONDS)
        else:
            self.close_reader()
            if self.diagnostics:
                self.diagnostics.close()
