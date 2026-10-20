"""Native point playback worker. UI status is queued; Tk stays on its own thread."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import queue
import threading

from utils.core.coordinate_points import ordered_waypoints, validate_collect_wait, validate_loop_count
from utils.core.coordinate_recovery import validate_recovery_options
from utils.core.coordinate_navigation import LocalNavigator
from utils.core import thresholds as policy
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.route_path_memory import RoutePathMemory
from utils.drivers.playback_history import PlaybackHistory, RecordingPosition
from utils.drivers.point_game_window import PointGameWindow
from utils.drivers.timeline_hotkeys import PlaybackControls
from utils.drivers.town_window import TimelineInput
from utils.drivers.windows_input import PreciseClock
from utils.ui.playback_diagnostics import PlaybackDiagnostics


class PointPlayback:
    def __init__(self, profile, runner=None, window_factory=PointGameWindow, controls_factory=PlaybackControls,
                 reader_factory=ReadOnlyPosition, device_factory=TimelineInput, clock=PreciseClock,
                 scene_provider=None, navigator_factory=LocalNavigator, diagnostics_path=None, repair_factory=None,
                 tool_factory=None, food_factory=None, wall_clock=None):
        self.profile = Path(profile)
        self.runner = runner
        self.window_factory, self.controls_factory = window_factory, controls_factory
        self.reader_factory, self.device_factory, self.clock = reader_factory, device_factory, clock
        self.scene_provider, self.navigator_factory = scene_provider, navigator_factory
        self.repair_factory = repair_factory
        self.tool_factory = tool_factory
        self.food_factory = food_factory
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        self.navigation_path = []
        self.path_memory = None
        self.history = None
        self.history_root = Path(diagnostics_path).parent.parent/'回放记录' if diagnostics_path is not None else None
        self.diagnostics = PlaybackDiagnostics(diagnostics_path) if diagnostics_path is not None else None
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
            if state == 'finished':
                completed_at = self.wall_clock()
                message += f' · 完成时间 {completed_at:%Y-%m-%d %H:%M:%S}'
                extra['completed_at'] = completed_at.isoformat(timespec='seconds')
            self.cycle = extra.get('cycle', self.cycle)
            if 'navigation_path' in extra:
                self.navigation_path = extra.pop('navigation_path')
            if state in ('finished', 'stopped', 'error', 'reached', 'cycle_finished',
                         'collecting', 'recovering', 'paused', 'blocked', 'repairing', 'repair_paused',
                         'equipping', 'equip_paused', 'eating', 'food_paused'):
                self.navigation_path = []
            self.take_latest()
            status = {'state': state, 'message': message, 'waypoint_count': self.total, 'cycle': self.cycle,
                      'loop_count': self.loop_count, 'navigation_path': self.navigation_path, **extra}
            if self.diagnostics:
                self.diagnostics.write(status)
            if self.history:
                self.history.event(status)
                status['history_path'] = str(self.history.path)
                if self.history.error:
                    status['message'] += f' · 回放记录写入失败：{self.history.error}'
            self.results.put_nowait(status)

    def take_latest(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def start(self, data, pid, start_mode='first', recovery_attempts=policy.COORD_RECOVERY_DEFAULT_ATTEMPTS,
              jump_on_recovery=True, collect_wait_seconds=policy.COORD_COLLECTION_DEFAULT_WAIT,
              loop_count=policy.COORD_PLAYBACK_DEFAULT_LOOPS, auto_navigate=False, auto_repair=False, auto_eat=False,
              route_path=None, remember_paths=True):
        if self.closed or self.active:
            raise ValueError('已有点位回放正在运行，请先停止。')
        if self.runner is None:
            raise ValueError('回放入口未装配，请从窗口版程序入口启动。')
        if start_mode not in ('first', 'nearest'):
            raise ValueError('起点模式无效。')
        validate_recovery_options(recovery_attempts, jump_on_recovery)
        if not recovery_attempts:
            raise ValueError('每轮脱困次数应为 1～10 次。')
        validate_collect_wait(collect_wait_seconds)
        validate_loop_count(loop_count)
        if type(auto_navigate) is not bool:
            raise ValueError('自动寻路选项无效。')
        if type(auto_repair) is not bool:
            raise ValueError('自动维修选项无效。')
        if type(auto_eat) is not bool:
            raise ValueError('自动吃水果选项无效。')
        if type(remember_paths) is not bool:
            raise ValueError('记忆走法选项无效。')
        if auto_navigate and self.scene_provider is None:
            raise ValueError('场景导航尚未连接，请重新打开新版程序。')
        self.total = len(ordered_waypoints(data))
        if type(pid) is not int or pid <= 0:
            raise ValueError('没有可用的游戏进程，请重新连接。')
        self.path_memory = (RoutePathMemory(route_path, data)
                            if auto_navigate and remember_paths and route_path is not None else None)
        self.stop_event.clear()
        self.cycle, self.loop_count = 1, loop_count
        self.navigation_path = []
        self.history = (PlaybackHistory(self.history_root, deepcopy(data),
                        Path(route_path).stem if route_path is not None else '未命名路线',
                        {'start_mode': start_mode, 'loop_count': loop_count,
                         'auto_navigate': auto_navigate, 'remember_paths': remember_paths,
                         'auto_repair': auto_repair, 'auto_eat': auto_eat,
                         'collect_wait_seconds': collect_wait_seconds,
                         'recovery_attempts': recovery_attempts, 'jump_on_recovery': jump_on_recovery})
                        if self.history_root is not None else None)
        self.update('playback_wait', f'已就绪：{self.total} 个点 · 循环 {loop_count} 次 · 切回游戏按 F6 开始 · F9 停止')
        self.thread = threading.Thread(target=self._run, args=(deepcopy(data), pid, start_mode,
                                                              recovery_attempts, jump_on_recovery, collect_wait_seconds, loop_count, auto_navigate, auto_repair, auto_eat),
                                       name='point-playback', daemon=True)
        self.thread.start()

    def _run(self, data, pid, start_mode, recovery_attempts, jump_on_recovery, collect_wait_seconds, loop_count, auto_navigate, auto_repair, auto_eat):
        controls, device = None, None
        try:
            window = self.window_factory(pid)
            size = window.size()
            device = self.device_factory(window)
            controls = self.controls_factory(window, size, self, self.stop_event.is_set)
            controls.start()
            navigation = ({'navigator': self.navigator_factory(lambda: self.scene_provider(pid), clock=self.clock.monotonic)}
                          if auto_navigate else {})
            if auto_navigate and self.path_memory is not None:
                navigation['navigator'].path_memory = self.path_memory
                navigation['navigator'].memory_state = 'learning'
            from utils.vision.axe_pick import LazyToolSelector
            navigation['tool_selector'] = LazyToolSelector(self.profile.parent/'axe_pick_icon.png', self.tool_factory)
            if auto_eat:
                from utils.vision.food_menu import FruitMenu
                navigation['food'] = LazyToolSelector(self.profile.parent/'food_bag_marker.png', self.food_factory or FruitMenu)
            if auto_repair:
                from utils.vision.repair_box import RepairBoxMenu
                navigation['repair_box'] = (self.repair_factory or RepairBoxMenu)(self.profile.parent/'repair_box_menu.png')
            mode_text = '最近点 → 末点' if start_mode == 'nearest' else f'点位 1 → {self.total}'
            if self.path_memory is not None:
                mode_text += f' · 已记忆 {self.path_memory.count} 段走法'
            self.update('playback_wait', f'{mode_text} · 循环 {loop_count} 次 · 切回游戏按 F6 开始 · F9 停止')
            completed = self.runner(data, size,
                                     lambda: self._recording_reader(pid, window.hwnd),
                                     device, window, controls, self.update, clock=self.clock, start_mode=start_mode,
                                     recovery_attempts=recovery_attempts, jump_on_recovery=jump_on_recovery,
                                     collect_wait_seconds=collect_wait_seconds, loop_count=loop_count,
                                     continuous_recovery=True, **navigation)
            finish_message = (('本次点位回放完成' if loop_count == 1 else f'已完成 {loop_count} 轮回放')
                              if completed else '回放已停止，按键已释放')
            if completed and any('special_resource' in point for point in data.get('points', [])):
                finish_message = '动态资源连按操作已完成，请核对实际掉落'
            if self.path_memory is not None:
                finish_message += f' · 已更新路线学习 · 已记忆 {self.path_memory.count} 段走法'
            self.update('finished' if completed else 'stopped', finish_message,
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
            if self.history:
                self.history.close()

    def _recording_reader(self, pid, hwnd):
        reader = self.reader_factory(pid, self.profile, hwnd=hwnd)
        return RecordingPosition(reader, self.history, self.clock.monotonic) if self.history else reader

    def stop(self):
        self.stop_event.set()

    def close(self):
        self.closed = True
        self.stop()
        if self.thread:
            self.thread.join(timeout=policy.COORD_MONITOR_RETRY_SECONDS)
        if self.diagnostics:
            self.diagnostics.close()
