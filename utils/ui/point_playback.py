"""Native point playback worker. UI status is queued; Tk stays on its own thread."""
from copy import deepcopy
from pathlib import Path
import queue
import threading

from utils.core.coordinate_points import ordered_waypoints, validate_collect_wait, validate_loop_count
from utils.core.coordinate_recovery import validate_recovery_options
from utils.core import thresholds as policy
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.point_game_window import PointGameWindow
from utils.drivers.timeline_hotkeys import PlaybackControls
from utils.drivers.town_window import TimelineInput
from utils.drivers.windows_input import PreciseClock


class PointPlayback:
    def __init__(self, profile, runner=None, window_factory=PointGameWindow, controls_factory=PlaybackControls,
                 reader_factory=ReadOnlyPosition, device_factory=TimelineInput, clock=PreciseClock):
        self.profile = Path(profile)
        self.runner = runner
        self.window_factory, self.controls_factory = window_factory, controls_factory
        self.reader_factory, self.device_factory, self.clock = reader_factory, device_factory, clock
        self.results = queue.Queue(maxsize=1)
        self.result_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.closed = False
        self.total = 0
        self.cycle, self.loop_count = 1, policy.COORD_PLAYBACK_DEFAULT_LOOPS

    @property
    def active(self):
        return bool(self.thread and self.thread.is_alive())

    def update(self, state, message, window=None, **extra):
        with self.result_lock:
            self.cycle = extra.get('cycle', self.cycle)
            self.take_latest()
            self.results.put_nowait({'state': state, 'message': message,
                                     'waypoint_count': self.total, 'cycle': self.cycle,
                                     'loop_count': self.loop_count, **extra})

    def take_latest(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def start(self, data, pid, start_mode='first', recovery_attempts=policy.COORD_RECOVERY_DEFAULT_ATTEMPTS,
              jump_on_recovery=True, collect_wait_seconds=policy.COORD_COLLECTION_DEFAULT_WAIT,
              loop_count=policy.COORD_PLAYBACK_DEFAULT_LOOPS):
        if self.closed or self.active:
            raise ValueError('已有点位回放正在运行，请先停止。')
        if self.runner is None:
            raise ValueError('回放入口未装配，请从窗口版程序入口启动。')
        if start_mode not in ('first', 'nearest'):
            raise ValueError('起点模式无效。')
        validate_recovery_options(recovery_attempts, jump_on_recovery)
        validate_collect_wait(collect_wait_seconds)
        validate_loop_count(loop_count)
        self.total = len(ordered_waypoints(data))
        if type(pid) is not int or pid <= 0:
            raise ValueError('没有可用的游戏进程，请重新连接。')
        self.stop_event.clear()
        self.cycle, self.loop_count = 1, loop_count
        self.update('playback_wait', f'已就绪：{self.total} 个点 · 循环 {loop_count} 次 · 切回游戏按 F6 开始 · F9 停止')
        self.thread = threading.Thread(target=self._run, args=(deepcopy(data), pid, start_mode,
                                                              recovery_attempts, jump_on_recovery, collect_wait_seconds, loop_count),
                                       name='point-playback', daemon=True)
        self.thread.start()

    def _run(self, data, pid, start_mode, recovery_attempts, jump_on_recovery, collect_wait_seconds, loop_count):
        controls, device = None, None
        try:
            window = self.window_factory(pid)
            size = window.size()
            device = self.device_factory(window)
            controls = self.controls_factory(window, size, self, self.stop_event.is_set)
            controls.start()
            mode_text = '最近点 → 末点' if start_mode == 'nearest' else f'点位 1 → {self.total}'
            self.update('playback_wait', f'{mode_text} · 循环 {loop_count} 次 · 切回游戏按 F6 开始 · F9 停止')
            completed = self.runner(data, size,
                                     lambda: self.reader_factory(pid, self.profile, hwnd=window.hwnd),
                                     device, window, controls, self.update, clock=self.clock, start_mode=start_mode,
                                     recovery_attempts=recovery_attempts, jump_on_recovery=jump_on_recovery,
                                     collect_wait_seconds=collect_wait_seconds, loop_count=loop_count)
            self.update('finished' if completed else 'stopped',
                        ('本次点位回放完成' if loop_count == 1 else f'已完成 {loop_count} 轮回放')
                        if completed else '回放已停止，按键已释放',
                        cycle=loop_count if completed else self.cycle)
        except Exception as exc:
            self.update('error', f'点位回放停止：{exc}')
        finally:
            for cleanup in [device.release_all if device else None, controls.close if controls else None]:
                if cleanup is not None:
                    try:
                        cleanup()
                    except Exception as exc:
                        self.update('error', f'回放清理失败：{exc}')

    def stop(self):
        self.stop_event.set()

    def close(self):
        self.closed = True
        self.stop()
        if self.thread:
            self.thread.join(timeout=policy.COORD_MONITOR_RETRY_SECONDS)
