"""Native point playback worker. UI status is queued; Tk stays on its own thread."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from collections import deque
import math
import queue
import threading

from utils.core.coordinate_points import ordered_waypoints, validate_collect_wait, validate_loop_count
from utils.core.coordinate_route import valid_position
from utils.core.coordinate_recovery import validate_recovery_options
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait
from utils.core.collection_navigation import CollectionNavigator
from utils.core import special_verdict
from utils.core.special_patrol import validate_patrol
from utils.core import thresholds as policy
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.route_path_memory import RoutePathMemory
from utils.drivers.playback_history import PlaybackHistory, RecordingPosition, GuidanceRecordingPosition
from utils.core.guidance_navigation import GuidanceNavigator
from utils.core.road_preference import load_road_preference
from utils.ui.scene_map_worker import SceneMapWorker
from utils.drivers.mono_scene import NavigationSceneMap
from utils.drivers.navigation_process import NavigationProcessScene
from utils.core.food_policy import FALLBACK_MODES
from utils.drivers.point_game_window import PointGameWindow
from utils.drivers.timeline_hotkeys import PlaybackControls
from utils.drivers.town_window import TimelineInput
from utils.drivers.windows_input import PreciseClock
from utils.ui.playback_diagnostics import PlaybackDiagnostics


SPECIAL_FINISH_TEXT = {
    None: '动态资源采集结束，请核对背包',
    special_verdict.GATHERED: '动态资源已确认采到，按键已释放',
    special_verdict.GONE: '动态资源已从场景消失，采集结果未确认',
    special_verdict.COOLING: '动态资源冷却已开始，产出未确认',
    special_verdict.CHANGED: '动态资源状态有变化但未见掉落标志，请核对背包',
    special_verdict.UNCHANGED: '动态资源连按未见状态变化，请核对背包',
    special_verdict.UNKNOWN: '动态资源状态未能读取，请核对背包',
}

BUS_GUIDANCE_SOFT_RADIUS = 5.0
BUS_GUIDANCE_SOFT_REMAINING = 6.0
BUS_GUIDANCE_SOFT_HEIGHT = 1.5
BUS_GUIDANCE_MAX_RETRIES = 2


class PointPlayback:
    def __init__(self, profile, runner=None, window_factory=PointGameWindow, controls_factory=PlaybackControls,
                 reader_factory=ReadOnlyPosition, device_factory=TimelineInput, clock=PreciseClock,
                 scene_provider=None, navigator_factory=LocalNavigator, diagnostics_path=None, repair_factory=None,
                 tool_factory=None, food_factory=None, wall_clock=None, scene_refresh=None, input_busy=None, input_gate=None,
                 guidance_runner=None, guidance_scene_factory=None):
        self.profile = Path(profile)
        self.runner = runner
        self.guidance_runner = guidance_runner
        self.guidance_scene_factory = guidance_scene_factory
        self.window_factory, self.controls_factory = window_factory, controls_factory
        self.reader_factory, self.device_factory, self.clock = reader_factory, device_factory, clock
        self.scene_provider, self.navigator_factory = scene_provider, navigator_factory
        self.scene_refresh = scene_refresh
        self.input_busy = input_busy or (lambda: False)
        self.input_gate = input_gate
        self.repair_factory = repair_factory
        self.tool_factory = tool_factory
        self.food_factory = food_factory
        self.food_fallback = 'fruit'
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        self.navigation_path = []
        self.path_memory = None
        self.history = None
        self.diagnostics_dir = Path(diagnostics_path).parent if diagnostics_path is not None else None
        self.history_root = self.diagnostics_dir.parent / '路线' if self.diagnostics_dir is not None else None
        self.diagnostics = PlaybackDiagnostics(diagnostics_path) if diagnostics_path is not None else None
        self.results = queue.Queue(maxsize=1)
        self.result_lock = threading.Lock()
        self.log_events = deque(maxlen=2000)
        self.log_dropped = 0
        self.log_signature = None
        self.stop_event = threading.Event()
        self.thread = None
        self.closed = False
        self.total = 0
        self.cycle, self.loop_count = 1, policy.COORD_PLAYBACK_DEFAULT_LOOPS
        self.special_outcome = None
        self.incomplete_count = 0
        self.patrol_result = None

    @property
    def active(self):
        return bool(self.thread and self.thread.is_alive())

    def update(self, state, message, window=None, **extra):
        with self.result_lock:
            if state in ('skipped', 'partial', 'reached', 'cycle_finished', 'cycle_partial'):
                self.incomplete_count = extra.get('skipped_count', self.incomplete_count)
            if state == 'finished':
                completed_at = self.wall_clock()
                message += f' · 完成时间 {completed_at:%Y-%m-%d %H:%M:%S}'
                extra['completed_at'] = completed_at.isoformat(timespec='seconds')
            self.cycle = extra.get('cycle', self.cycle)
            if 'resource_outcome' in extra:
                self.special_outcome = extra['resource_outcome']
            if 'patrol_result' in extra:
                self.patrol_result = extra['patrol_result']
            if 'navigation_path' in extra:
                self.navigation_path = extra.pop('navigation_path')
            if state in ('finished', 'stopped', 'error', 'partial', 'skipped', 'reached', 'cycle_finished', 'cycle_partial',
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
                if self.history.archive_error:
                    status['message'] += f' · 路线学习索引写入失败：{self.history.archive_error}'
            self.results.put_nowait(status)
            self._record_log(status)

    def _record_log(self, status):
        """Called under result_lock. Copy only the small fields used by the UI."""
        try:
            state, message = status['state'], status['message']
            # Countdown ticks are useful in the status bar, but one log entry
            # per wait is sufficient. The next collecting event starts a new wait.
            signature = (state, None if state == 'collection_wait' else message,
                         status.get('cycle'), status.get('waypoint'))
            if signature == self.log_signature:
                return
            event = {k: status[k] for k in ('state', 'message', 'cycle', 'loop_count',
                     'waypoint', 'waypoint_count', 'skipped_count') if k in status}
            event['at'] = datetime.now().astimezone().isoformat(timespec='seconds')
            if len(self.log_events) == self.log_events.maxlen:
                self.log_dropped += 1
            self.log_events.append(event)
            self.log_signature = signature
        except Exception:
            # Display logging must not interrupt a playback or its cleanup.
            pass

    def record_log(self, state, message):
        """UI preparation and startup errors do not replace task status."""
        with self.result_lock:
            self._record_log({'state': state, 'message': message})

    def take_log_events(self, limit=100):
        with self.result_lock:
            events = []
            if self.log_dropped and limit > 0:
                events.append({'state': 'partial', 'message':
                               f'日志缓冲已满，已省略最早 {self.log_dropped} 条待显示记录'})
                self.log_dropped = 0
            events.extend(self.log_events.popleft()
                          for _ in range(min(max(0, limit - len(events)), len(self.log_events))))
            return events

    def take_latest(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def start(self, data, pid, start_mode='first', recovery_attempts=policy.COORD_RECOVERY_DEFAULT_ATTEMPTS,
              jump_on_recovery=True, collect_wait_seconds=policy.COORD_COLLECTION_DEFAULT_WAIT,
              loop_count=policy.COORD_PLAYBACK_DEFAULT_LOOPS, auto_navigate=False, auto_repair=False, auto_eat=False,
              route_path=None, remember_paths=True, food_fallback='fruit', walk_to_first=False):
        if self.closed or self.active or self.input_busy():
            raise ValueError('已有点位回放正在运行，请先停止。')
        if self.runner is None:
            raise ValueError('回放入口未装配，请从窗口版程序入口启动。')
        if start_mode not in ('first', 'nearest'):
            raise ValueError('起点模式无效。')
        validate_recovery_options(recovery_attempts, jump_on_recovery)
        if not recovery_attempts and not data.get('recorded_walk'):
            raise ValueError('每轮脱困次数应为 1～10 次。')
        validate_collect_wait(collect_wait_seconds)
        validate_loop_count(loop_count)
        if type(auto_navigate) is not bool:
            raise ValueError('自动寻路选项无效。')
        if type(walk_to_first) is not bool:
            raise ValueError('先走到首点选项无效。')
        if walk_to_first and not auto_navigate:
            raise ValueError('「先走到首点」需要开启自动寻路。')
        if type(auto_repair) is not bool:
            raise ValueError('自动维修选项无效。')
        if food_fallback not in FALLBACK_MODES:
            raise ValueError('无料理时的食物范围无效')
        self.food_fallback = food_fallback
        if type(auto_eat) is not bool:
            raise ValueError('自动吃水果选项无效。')
        if type(remember_paths) is not bool:
            raise ValueError('记忆走法选项无效。')
        if data.get('recorded_walk') and (
                not auto_navigate or remember_paths or recovery_attempts or jump_on_recovery
                or start_mode != 'first' or loop_count != 1 or auto_repair or auto_eat):
            raise ValueError('录制路线需要独立的原路回放配置')
        if auto_navigate and self.scene_provider is None:
            raise ValueError('场景导航尚未连接，请重新打开新版程序。')
        self.total = len(ordered_waypoints(data))
        if 'special_patrol' in data:
            validate_patrol(data)
            if not auto_navigate or remember_paths:
                raise ValueError('房门巡查必须启用实时寻路并关闭该任务的走法记忆')
        if type(pid) is not int or pid <= 0:
            raise ValueError('没有可用的游戏进程，请重新连接。')
        self.path_memory = (RoutePathMemory(route_path, data)
                            if auto_navigate and remember_paths and route_path is not None else None)
        self.stop_event.clear()
        self.cycle, self.loop_count = 1, loop_count
        self.special_outcome = None
        self.patrol_result = None
        self.incomplete_count = 0
        self.navigation_path = []
        history_root = self.history_root
        if history_root is not None and data.get('recorded_walk'):
            history_root = history_root / 'recorded'
        self.history = (PlaybackHistory(history_root, deepcopy(data),
                        Path(route_path).stem if route_path is not None else '未命名路线',
                        {'start_mode': start_mode, 'loop_count': loop_count,
                         'auto_navigate': auto_navigate, 'remember_paths': remember_paths,
                         'auto_repair': auto_repair, 'auto_eat': auto_eat, 'food_fallback': food_fallback,
                         'walk_to_first': walk_to_first,
                         'collect_wait_seconds': collect_wait_seconds,
                         'recovery_attempts': recovery_attempts, 'jump_on_recovery': jump_on_recovery})
                        if history_root is not None else None)
        if route_path is not None:
            self.record_log('preparation', f'当前路线：{Path(route_path).stem}')
        self.update('playback_wait', f'已就绪：{self.total} 个点 · 循环 {loop_count} 次 · 切回游戏按 F6 开始 · F9 停止')
        self.thread = threading.Thread(target=self._run, args=(deepcopy(data), pid, start_mode,
                                                              recovery_attempts, jump_on_recovery, collect_wait_seconds, loop_count, auto_navigate, auto_repair, auto_eat,
                                                              walk_to_first),
                                       name='point-playback', daemon=True)
        self.thread.start()

    def _run(self, *args):
        if self.input_gate is None:
            return self._execute(*args)
        try:
            with self.input_gate.claim('点位回放'):
                return self._execute(*args)
        except ValueError as exc:
            self.update('error', str(exc))
            if self.history:
                self.history.close()

    def start_guidance(self, pid, *, map_opening_only=False, first_target=None, bus_target=None):
        if self.closed or self.active or self.input_busy():
            raise ValueError('已有任务正在运行，请先停止。')
        if type(pid) is not int or pid <= 0:
            raise ValueError('没有可用的游戏进程，请重新连接。')
        if not map_opening_only and self.scene_provider is None:
            raise ValueError('场景碰撞检查尚未连接，不能开始游戏引导跟随。')
        if not map_opening_only and not callable(self.guidance_runner):
            raise ValueError('游戏引导入口未装配，请从新版窗口程序启动。')
        self.stop_event.clear()
        self.path_memory = None
        self.cycle, self.loop_count, self.total = 1, 1, 0
        self.special_outcome = self.patrol_result = None
        self.incomplete_count = 0
        self.navigation_path = []
        self.history = (PlaybackHistory(self.history_root, {'engine': 'native_guidance', 'points': []},
                        '跟随游戏引导', {'manual_start': True, 'no_interaction': True})
                        if self.history_root is not None else None)
        self.update('playback_wait', ('切回游戏按 F6 准备地图并检查首点公交追踪 · F9停止' if bus_target is not None
                                     else '切回游戏按 F6 开图并标记路线首点 · F9停止' if first_target is not None
                                     else '切回游戏按 F6 开图、缩小并居中 · F9停止' if map_opening_only
                                     else '正在缓存完整游戏引导路线 · 准备完成后按 F6 开始 · F9停止'),
                    navigation_mode='native_guidance')
        self.thread = threading.Thread(target=self._run_guidance, args=(pid, map_opening_only, first_target, bus_target),
                                       name='native-guidance-follow', daemon=True)
        self.thread.start()

    def _run_guidance(self, pid, map_opening_only=False, first_target=None, bus_target=None):
        if self.input_gate is None:
            return self._execute_guidance(pid, map_opening_only, first_target, bus_target)
        try:
            with self.input_gate.claim('游戏引导跟随'):
                return self._execute_guidance(pid, map_opening_only, first_target, bus_target)
        except ValueError as exc:
            self.update('error', str(exc), navigation_mode='native_guidance')
            if self.history:
                self.history.close()

    def _execute_guidance(self, pid, map_opening_only=False, first_target=None, bus_target=None):
        controls, device = None, None
        navigation_worker = None
        def report(state, message, **extra):
            extra.pop('waypoint', None)  # Never highlight an unrelated saved point.
            extra.update(waypoint_count=0, navigation_mode='native_guidance')
            if navigation_worker is not None:
                pause_metrics = getattr(navigation_worker, 'pause_navigation_metrics', None)
                metrics = getattr(navigation_worker, 'navigation_metrics', None)
                if state in ('paused', 'finished', 'stopped', 'error') and callable(pause_metrics):
                    pause_metrics()
                if callable(metrics):
                    extra['navigation_supply'] = metrics(pid)
            self.update(state, message, **extra)
        class Feedback:
            def update(self, state, message, **extra):
                report(state, message, **extra)
        try:
            window = self.window_factory(pid)
            size = window.size()
            device = self.device_factory(window)
            input_observer = getattr(self.history, 'input_edge', None)
            if isinstance(device, TimelineInput) and callable(input_observer):
                device.input_observer = input_observer
            controls = self.controls_factory(window, size, Feedback(), self.stop_event.is_set)
            controls.start()
            if map_opening_only:
                from utils.flows.map_targeting import MapTargeter

                while True:
                    running, stopped = controls.snapshot()
                    if stopped or self.stop_event.is_set():
                        report('stopped', '地图准备已停止')
                        return
                    if running:
                        break
                    self.clock.sleep(.01)

                def check_map_input():
                    running, stopped = controls.snapshot()
                    if stopped or not running or self.stop_event.is_set():
                        raise ValueError('地图准备已停止或暂停')

                reader = self.reader_factory(pid, self.profile, hwnd=window.hwnd)
                try:
                    targeter = MapTargeter(reader, device, window, controls,
                                           self.clock, check_map_input, report)
                    result = targeter.open_and_zoom_once(first_target, center_map=first_target is None,
                                                         bus_target=bus_target)
                    if 'map_bus_id' in result:
                        message = f'已确认公交站 {result["map_bus_id"]} 追踪，地图保持打开'
                    elif bus_target is not None:
                        message = '首点距离不足 50，地图已居中'
                    elif first_target is None:
                        message = '已确认地图缩小并居中，地图保持打开'
                    elif result['map_marker_reused']:
                        message = '已核对首点现有标记，地图保持打开'
                    else:
                        message = '已确认首点标记保存，地图保持打开'
                    report('finished', message, **result)
                finally:
                    reader.close()
                return
            provider = lambda:self.scene_provider(pid)
            if self.guidance_scene_factory is not None:
                factory = (NavigationProcessScene if self.guidance_scene_factory is NavigationSceneMap
                           else self.guidance_scene_factory)
                navigation_worker = SceneMapWorker(self.profile, factory=factory,
                    diagnostics_path=self.diagnostics_dir / '引导碰撞读取.jsonl' if self.diagnostics_dir is not None else None)
                navigation_worker.set_pid(pid)
                navigation_worker.start()
                provider = lambda:navigation_worker.navigation_snapshot(pid)
            factory = GuidanceNavigator if self.navigator_factory is LocalNavigator else self.navigator_factory
            options = {'respect_water': False} if factory is GuidanceNavigator else {}
            navigator = factory(provider, clock=self.clock.monotonic, **options)
            result = self.guidance_runner(size,
                lambda: self._recording_reader(pid, window.hwnd, guidance=True), device, window, controls,
                report, clock=self.clock, navigator=navigator)
            message = result['reason']
            if result['arrived']:
                message += f' · 距追踪目标约 {result["target_distance"]:.1f} 单位'
            report('finished' if result['arrived'] else 'stopped', message)
        except Exception as exc:
            report('error', f'游戏引导跟随停止：{exc}')
        finally:
            for cleanup in [device.release_all if device else None, controls.close if controls else None]:
                if cleanup is not None:
                    try:
                        cleanup()
                    except Exception as exc:
                        report('error', f'游戏引导清理失败：{exc}')
            if self.history:
                self.history.close()

            if navigation_worker:
                navigation_worker.close()

    def _execute(self, data, pid, start_mode, recovery_attempts, jump_on_recovery, collect_wait_seconds, loop_count, auto_navigate, auto_repair, auto_eat, walk_to_first=False):
        controls, device, navigation_worker = None, None, None
        def report(state,message,**extra):
            if navigation_worker is not None:
                extra['navigation_supply']=navigation_worker.navigation_metrics(pid)
                if state in ('paused','finished','stopped','error'):
                    navigation_worker.pause_navigation_metrics()
            self.update(state,message,**extra)
        try:
            window = self.window_factory(pid)
            size = window.size()
            device = self.device_factory(window)
            input_observer = getattr(self.history, 'input_edge', None)
            if isinstance(device, TimelineInput) and callable(input_observer):
                device.input_observer = input_observer
            controls = self.controls_factory(window, size, self, self.stop_event.is_set)
            controls.start()
            factory = CollectionNavigator if self.navigator_factory is LocalNavigator else self.navigator_factory
            recorded = data.get('recorded_walk', False)
            options = {}
            if recorded:
                from utils.core.recorded_walk_navigation import RecordedWalkNavigator
                factory = RecordedWalkNavigator
            elif auto_navigate and factory is CollectionNavigator:
                options = {'native_map_collection': False,
                           'road_preference': load_road_preference(self.profile.parent)}
            navigation = ({'navigator': factory(lambda: self.scene_provider(pid), clock=self.clock.monotonic, **options)}
                          if auto_navigate else {})
            if auto_navigate and isinstance(navigation['navigator'],CollectionNavigator) \
                    and self.guidance_scene_factory is not None:
                factory=(NavigationProcessScene if self.guidance_scene_factory is NavigationSceneMap
                         else self.guidance_scene_factory)
                navigation_worker=SceneMapWorker(self.profile,factory=factory,
                    diagnostics_path=self.diagnostics_dir / '采集引导碰撞读取.jsonl' if self.diagnostics_dir is not None else None)
                navigation_worker.set_pid(pid);navigation_worker.start()
                navigation['navigator'].native_scene_provider=lambda:navigation_worker.navigation_snapshot(pid)
                if navigation['navigator'].hybrid_collection:
                    navigation['navigator'].scene_provider=navigation['navigator'].native_scene_provider
            if auto_navigate and self.scene_refresh is not None:
                navigation['scene_refresh'] = lambda: self.scene_refresh(pid)
            if auto_navigate and self.path_memory is not None:
                navigation['navigator'].path_memory = self.path_memory
                navigation['navigator'].memory_state = 'learning'
            from utils.vision.axe_pick import LazyToolSelector
            if not recorded:
                navigation['tool_selector'] = LazyToolSelector(
                    self.profile.parent/'axe_pick_icon.png', self.tool_factory)
            if auto_eat:
                from utils.vision.first_slot_food import FirstSlotFoodMenu
                navigation['food_fallback'] = self.food_fallback
                navigation['food'] = LazyToolSelector(self.profile.parent/'food_bag_marker.png', self.food_factory or FirstSlotFoodMenu)
            if auto_repair:
                from utils.vision.repair_box import RepairBoxMenu
                navigation['repair_box'] = (self.repair_factory or RepairBoxMenu)(self.profile.parent/'repair_box_menu.png')
            mode_text = '最近点 → 末点' if start_mode == 'nearest' else f'点位 1 → {self.total}'
            if self.path_memory is not None:
                mode_text += f' · 已记忆 {self.path_memory.count} 段走法'
            self.update('playback_wait', f'{mode_text} · 循环 {loop_count} 次 · 切回游戏按 F6 开始 · F9 停止')
            if not recorded and 'special_patrol' not in data and self.guidance_runner is not None:
                # Keep the existing F6 start gate. Once the user actually starts
                # replay, use native transit guidance only when the first target
                # is far away; the existing route runner still owns collection.
                while True:
                    running, stopped = controls.snapshot()
                    if stopped or self.stop_event.is_set():
                        report('stopped', '回放开始前已停止，按键已释放')
                        return
                    if running:
                        break
                    self.clock.sleep(.01)

                waypoints = ordered_waypoints(data)
                first_target = tuple(waypoints[0]['position'])
                position_reader = self.reader_factory(pid, self.profile, hwnd=window.hwnd)
                try:
                    player_position = position_reader.read_for_playback()
                finally:
                    position_reader.close()
                if not valid_position(player_position):
                    raise ValueError('无法读取人物当前位置，未启动远距离公交追踪')
                distance_to_first = math.hypot(player_position[0]-first_target[0],
                                               player_position[2]-first_target[2])
                report('playback_wait', '已检查回放首点距离',
                       route_first_point=list(first_target),
                       route_first_distance_xz=distance_to_first,
                       bus_guidance_threshold=50.)
                if distance_to_first > 50. and walk_to_first:
                    # 用户选择先走到首点：不开地图、不追公交，交给本地寻路
                    # 直接逼近首点，到达后由常规采集流程接管。
                    report('playback_wait', '已跳过公交追踪，由本地寻路直接走到首点',
                           route_first_point=list(first_target),
                           route_first_distance_xz=distance_to_first,
                           walk_to_first=True)
                elif distance_to_first > 50.:
                    from utils.flows.map_targeting import MapTargeter

                    report('map_targeting', '首点距离超过 50，准备追踪最近公交站',
                           route_first_point=list(first_target),
                           route_first_distance_xz=distance_to_first)

                    def check_map_input():
                        running, stopped = controls.snapshot()
                        if stopped or not running or self.stop_event.is_set():
                            raise ValueError('远距离公交追踪期间回放已暂停或停止')

                    guidance_provider = (lambda: navigation_worker.navigation_snapshot(pid)) \
                        if navigation_worker is not None else (lambda: self.scene_provider(pid))
                    guide_navigator = GuidanceNavigator(guidance_provider,
                        clock=self.clock.monotonic, respect_water=False)

                    def preflight_first_collection_point():
                        local_navigator = navigation.get('navigator')
                        if not auto_navigate or local_navigator is None \
                                or not callable(getattr(local_navigator, 'next_target', None)):
                            raise ValueError('首点接管前没有可用的本地寻路器')
                        fresh_reader = self.reader_factory(pid, self.profile, hwnd=window.hwnd)
                        try:
                            current = fresh_reader.read_for_playback()
                        finally:
                            fresh_reader.close()
                        if not valid_position(current):
                            raise ValueError('首点接管前人物坐标无效')
                        check_map_input()
                        # This is a no-input planning preflight. The ordinary route
                        # runner replans again before moving, with its normal guards.
                        first_leg = local_navigator.next_target(
                            tuple(current), first_target, check=check_map_input,
                            before_plan=device.release_all)
                        if not valid_position(first_leg):
                            raise ValueError('本地寻路器未给出有效的首点接近目标')
                        return tuple(current), tuple(first_leg)

                    guide_result = None
                    preflight_position = preflight_target = None
                    guide_attempt_limit = BUS_GUIDANCE_MAX_RETRIES + 1
                    for guide_attempt in range(1, guide_attempt_limit + 1):
                        map_reader = self.reader_factory(pid, self.profile, hwnd=window.hwnd)
                        try:
                            targeter = MapTargeter(map_reader, device, window, controls,
                                                   self.clock, check_map_input, report)
                            map_result = targeter.open_and_zoom_once(
                                center_map=True, bus_target=first_target, close_after=True)
                        finally:
                            map_reader.close()

                        report('navigating', '公交站追踪已启动，开始跟随游戏引导',
                               bus_guide_attempt=guide_attempt,
                               bus_guide_attempt_limit=guide_attempt_limit, **map_result)
                        runner_options = {'clock': self.clock, 'navigator': guide_navigator}
                        if getattr(self.guidance_runner, 'supports_bus_soft_arrival', False):
                            runner_options['inactive_soft_arrival'] = {
                                'radius': BUS_GUIDANCE_SOFT_RADIUS,
                                'remaining': BUS_GUIDANCE_SOFT_REMAINING,
                                'height': BUS_GUIDANCE_SOFT_HEIGHT,
                            }
                        guide_result = self.guidance_runner(
                            size, lambda: self._recording_reader(pid, window.hwnd, guidance=True),
                            device, window, controls, report, **runner_options)

                        if guide_result.get('arrived'):
                            try:
                                preflight_position, preflight_target = preflight_first_collection_point()
                            except (OSError, ValueError, TypeError, RuntimeError, NavigationWait) as exc:
                                report('stopped', '公交引导结束，但首个采集点未通过本地碰撞路径预检',
                                       guidance_reason=guide_result.get('reason'),
                                       first_point_preflight_error=str(exc),
                                       route_first_point=list(first_target),
                                       soft_arrival=guide_result.get('soft_arrival', False))
                                return
                            break

                        if not guide_result.get('recoverable'):
                            report('stopped', '公交站引导未完成，未启动后续采集回放',
                                   guidance_reason=guide_result.get('reason'),
                                   bus_guide_attempt=guide_attempt,
                                   route_first_point=list(first_target))
                            return

                        try:
                            preflight_position, preflight_target = preflight_first_collection_point()
                        except (OSError, ValueError, TypeError, RuntimeError, NavigationWait) as exc:
                            if guide_attempt >= guide_attempt_limit:
                                report('stopped', '公交引导恢复额度耗尽，首点本地碰撞路径仍不可用',
                                       guidance_reason=guide_result.get('reason'),
                                       first_point_preflight_error=str(exc),
                                       bus_guide_attempt=guide_attempt,
                                       bus_guide_attempt_limit=guide_attempt_limit,
                                       route_first_point=list(first_target))
                                return
                            report('recovering', '公交追踪已结束且首点暂不可接管，重新开启地图并重追公交站',
                                   guidance_reason=guide_result.get('reason'),
                                   first_point_preflight_error=str(exc),
                                   bus_guide_attempt=guide_attempt,
                                   bus_guide_attempt_limit=guide_attempt_limit,
                                   recovery_attempt=guide_attempt,
                                   recovery_limit=BUS_GUIDANCE_MAX_RETRIES)
                            guide_navigator.invalidate()
                            continue
                        report('navigating', '公交追踪结束后首点通过本地碰撞路径预检，交给采集路线',
                               guidance_reason=guide_result.get('reason'),
                               bus_guide_direct_takeover=True,
                               bus_guide_attempt=guide_attempt,
                               route_first_point=list(first_target))
                        break

                    if preflight_position is None or preflight_target is None:
                        report('stopped', '公交引导恢复后未建立首点接管计划，未启动采集回放',
                               guidance_reason=(guide_result or {}).get('reason'),
                               bus_guide_attempt_limit=guide_attempt_limit,
                               route_first_point=list(first_target))
                        return
                    first_distance = math.hypot(preflight_position[0]-first_target[0],
                                                preflight_position[2]-first_target[2])
                    report('navigating', '已确认首点本地路径可规划，继续原采集路线',
                           guidance_target_distance=(guide_result or {}).get('target_distance'),
                           guidance_soft_arrival=(guide_result or {}).get('soft_arrival', False),
                           guidance_remaining_route=(guide_result or {}).get('remaining_route'),
                           guidance_height_difference=(guide_result or {}).get('height_difference'),
                           route_first_point=list(first_target),
                           route_first_distance_xz=first_distance,
                           first_leg_target=list(preflight_target),
                           first_point_height_difference=abs(preflight_position[1]-first_target[1]))
            completed = self.runner(data, size,
                                     lambda: self._recording_reader(pid, window.hwnd),
                                     device, window, controls, report, clock=self.clock, start_mode=start_mode,
                                     recovery_attempts=recovery_attempts, jump_on_recovery=jump_on_recovery,
                                     collect_wait_seconds=collect_wait_seconds, loop_count=loop_count,
                                     continuous_recovery=not recorded, **navigation)
            finish_message = (('本次点位回放完成' if loop_count == 1 else f'已完成 {loop_count} 轮回放')
                              if completed else '回放已停止，按键已释放')
            if not completed and self.incomplete_count:
                finish_message = f'回放结束，{self.incomplete_count} 个点位流程未完成，详见回放记录'
            if 'special_patrol' in data:
                finish_message = {'gathered': '已确认采到目标，巡查结束',
                                  'not_found': '本轮巡查结束，未发现目标；不代表全图没有',
                                  'partial': '巡查未完成，存在未到达房门',
                                  'unconfirmed': '已尝试采集，结果未确认',
                                  'partial_resources': '本轮只确认采到一种资源，另一种未完成，详见巡查记录',
                                  'data_unavailable': '场景数据持续未确认，请重新连接后重试'}.get(
                                      self.patrol_result, '巡查已停止，按键已释放')
            elif completed and any('special_resource' in point for point in data.get('points', [])):
                finish_message = SPECIAL_FINISH_TEXT.get(self.special_outcome, SPECIAL_FINISH_TEXT[None])
            if self.path_memory is not None:
                finish_message += f' · 已更新路线学习 · 已记忆 {self.path_memory.count} 段走法'
            self.update('finished' if completed else 'partial' if self.incomplete_count or self.patrol_result else 'stopped', finish_message,
                        cycle=loop_count if completed else self.cycle, skipped_count=self.incomplete_count)
        except Exception as exc:
            self.update('error', f'点位回放停止：{exc}')
        finally:
            for cleanup in [device.release_all if device else None, controls.close if controls else None]:
                if cleanup is not None:
                    try:
                        cleanup()
                    except Exception as exc:
                        self.update('error', f'回放清理失败：{exc}')
            if self.path_memory is not None:
                try:
                    self.path_memory.flush_rejections()
                except (OSError, ValueError) as exc:
                    if self.diagnostics:
                        self.diagnostics.write({'state': 'memory_write_warning',
                                                'message': f'走法遇阻记录暂未保存：{exc}'})
            if self.history:
                self.history.close()

            if navigation_worker:
                navigation_worker.close()

    def _recording_reader(self, pid, hwnd, guidance=False):
        reader = self.reader_factory(pid, self.profile, hwnd=hwnd)
        wrapper = GuidanceRecordingPosition if guidance else RecordingPosition
        return wrapper(reader, self.history, self.clock.monotonic) if self.history else reader

    def stop(self):
        self.stop_event.set()

    def close(self):
        self.closed = True
        self.stop()
        if self.thread:
            self.thread.join(timeout=policy.COORD_MONITOR_RETRY_SECONDS)
        if self.diagnostics:
            self.diagnostics.close()
