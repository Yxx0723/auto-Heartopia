"""Owned read-only collision process; no GUI, key injection or stale fallback."""
import math
import multiprocessing
import os
import pickle
import threading
import time

from utils.core import thresholds as policy
from utils.core.scene_read import SceneReadIssue, issue_summary, navigation_quality_valid
from utils.drivers.mono_scene import MemorySceneMap, NavigationSceneMap


def _child(connection, stop, game_pid, profile, factory, max_bytes):
    reader = None
    try:
        while not stop.is_set():
            if not connection.poll(policy.COORD_NAV_PROCESS_POLL_SECONDS):
                continue
            request = connection.recv()
            if not isinstance(request, tuple) or len(request) != 2 or request[0] != 'snapshot':
                break
            started = time.monotonic()
            frame = {'request': request[1], 'game_pid': game_pid, 'worker_pid':os.getpid()}
            try:
                if reader is None:
                    reader = factory(game_pid, profile)
                    reader.cancelled = stop.is_set
                frame['scene'] = reader.snapshot()
            except (OSError, ValueError) as exc:
                frame['error'] = issue_summary(exc)
            except Exception:
                frame['error'] = {'kind':'worker_error', 'stage':'navigation_process',
                                  'retryable':False, 'scope_verified':False}
            frame['stats'] = getattr(reader, 'last_read_stats', {}) if reader else {}
            frame['child_read_seconds'] = max(0., time.monotonic()-started)
            if stop.is_set():
                break
            raw = pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL)
            if len(raw) > max_bytes:
                frame.pop('scene', None)
                frame['error'] = {'kind':'transport_limit', 'stage':'navigation_process',
                                  'retryable':False, 'scope_verified':False}
                raw = pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL)
            if len(raw) > max_bytes:
                break
            connection.send_bytes(raw)
    except (EOFError, OSError):
        pass
    finally:
        if reader is not None:
            reader.close()
        connection.close()


class NavigationProcessScene(MemorySceneMap):
    """MemorySceneMap-compatible proxy. Only the child opens a game handle.

    Existing SceneMapWorker owns this proxy and retains its quality/age gates.
    The dedicated pipe carries complete snapshots with ORIGINAL timestamps.
    """
    navigation_only = True

    def __init__(self, pid, profile, *, reader_factory=NavigationSceneMap,
                 timeout=policy.COORD_NAV_PROCESS_TIMEOUT_SECONDS,
                 max_bytes=policy.COORD_NAV_PROCESS_MAX_BYTES):
        if type(pid) is not int or pid <= 0:
            raise ValueError('独立碰撞进程的游戏 PID 无效')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= policy.COORD_NAV_PROCESS_TIMEOUT_SECONDS:
            raise ValueError('独立碰撞进程超时无效')
        if type(max_bytes) is not int or not 1024 <= max_bytes <= policy.COORD_NAV_PROCESS_MAX_BYTES:
            raise ValueError('独立碰撞进程消息上限无效')
        self.pid, self.timeout, self.max_bytes = pid, timeout, max_bytes
        self.cancelled = lambda: False
        self.last_read_stats, self.sequence = {}, 0
        self.closed, self.close_lock = False, threading.Lock()
        context = multiprocessing.get_context('spawn')
        self.stop = context.Event()
        self.connection, remote = context.Pipe(duplex=True)
        self.process = context.Process(target=_child,
            args=(remote, self.stop, pid, str(profile), reader_factory, max_bytes),
            name='guidance-collision-process', daemon=True)
        try:
            self.process.start()
        except BaseException:
            remote.close(); self.connection.close()
            raise
        remote.close()

    @staticmethod
    def failure(kind):
        return SceneReadIssue('独立碰撞读取暂不可用', kind=kind, stage='navigation_process')

    def snapshot(self):
        if self.closed:
            raise self.failure('worker_closed')
        started = time.monotonic()
        self.sequence += 1
        request = self.sequence
        try:
            self.connection.send(('snapshot', request))
            while True:
                if self.closed or self.cancelled():
                    self.close()
                    raise self.failure('cancelled')
                if time.monotonic()-started >= self.timeout:
                    self.close()
                    raise self.failure('worker_timeout')
                if self.connection.poll(policy.COORD_NAV_PROCESS_POLL_SECONDS):
                    raw = self.connection.recv_bytes(self.max_bytes)
                    try:
                        frame = pickle.loads(raw)  # Private pipe from our owned child only.
                    except (pickle.PickleError, EOFError, ValueError, TypeError) as exc:
                        raise self.failure('worker_protocol') from exc
                    break
                if not self.process.is_alive():
                    raise self.failure('worker_exit')
        except (EOFError, OSError, ValueError) as exc:
            if isinstance(exc, SceneReadIssue):
                raise
            if self.closed or self.cancelled():
                raise self.failure('cancelled') from exc
            raise self.failure('worker_exit') from exc
        if not isinstance(frame, dict) or frame.get('request') != request or frame.get('game_pid') != self.pid:
            raise self.failure('worker_protocol')
        stats = frame.get('stats')
        self.last_read_stats = dict(stats) if isinstance(stats, dict) else {}
        self.last_read_stats['process'] = {'child_pid': self.process.pid,
            'worker_pid': frame.get('worker_pid'),
            'child_read_seconds': frame.get('child_read_seconds'), 'wire_bytes': len(raw),
            'request_seconds': max(0., time.monotonic()-started)}
        if 'error' in frame:
            error = frame['error']
            if not isinstance(error, dict) or any(key not in error for key in ('kind','stage','retryable','scope_verified')):
                raise self.failure('worker_protocol')
            if any(not isinstance(error[key], str) or not 1 <= len(error[key]) <= 128 for key in ('kind','stage')) \
                    or any(type(error[key]) is not bool for key in ('retryable','scope_verified')):
                raise self.failure('worker_protocol')
            issue = SceneReadIssue('独立碰撞读取未完成', **{key:error[key] for key in
                ('kind','stage','retryable','scope_verified')})
            if 'code' in error:
                issue.code = error['code']
            raise issue
        scene = frame.get('scene')
        if not isinstance(scene, dict) or scene.get('navigation_only') is not True:
            raise self.failure('worker_protocol')
        return scene

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            self.closed = True
            self.stop.set()
            try:
                self.process.join(policy.COORD_NAV_PROCESS_CLOSE_SECONDS)
                if self.process.is_alive():
                    self.process.terminate()  # Only our owned reader; never the game.
                    self.process.join(policy.COORD_NAV_PROCESS_CLOSE_SECONDS)
            finally:
                self.connection.close()

    interrupt = close


def probe_navigation_process(profile):
    """Frozen-package read-only preflight. No Tk, window activation or input."""
    from utils.drivers.coordinate_process import find_game_processes
    from utils.core.prepared_navigation import PreparedNavigationScene
    from utils.core.coordinate_navigation import scene_geometry_signature
    pids = find_game_processes()
    if len(pids) != 1:
        raise ValueError('预检需要唯一游戏进程')
    source = NavigationProcessScene(pids[0], profile)
    result = {'read_only':True, 'input_sent':False, 'parent_pid':os.getpid(), 'scans':[]}
    try:
        for _ in range(4):
            start = time.monotonic();scene = source.snapshot();read_finished = time.monotonic()
            prepared = PreparedNavigationScene(scene)
            prepared_at = time.monotonic()
            same = prepared.prepared_signature() == scene_geometry_signature(scene)
            if not same:
                raise ValueError('Prepared geometry signature mismatch')
            now = time.monotonic()
            result['scans'].append({'seconds':now-start, 'read_seconds':read_finished-start,
                'geometry_preparation_seconds':prepared_at-read_finished, 'prepared_signature_equal':same,
                'usable':navigation_quality_valid(scene,now), 'colliders':len(scene.get('colliders',[])),
                'remaining_ttl':min(scene['quality']['parts'][key]['observed_from']
                    for key in ('managed_collisions','native_collisions'))+policy.COORD_NAV_SCENE_MAX_AGE-now,
                'process':source.last_read_stats['process']})
        result['state'] = 'ready'
    finally:
        source.close()
        result['child_exited'] = not source.process.is_alive()
    return result
