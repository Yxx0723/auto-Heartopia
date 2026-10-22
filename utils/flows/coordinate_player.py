"""Position-feedback session, steering and runner assembly with injected devices."""
import math
from pathlib import Path
from utils.core import thresholds as policy
from utils.core.coordinate_points import choose_start_index, ordered_waypoints, validate_collect_wait, validate_loop_count
from utils.core.coordinate_navigation import NavigationWait, ObstacleMap
from utils.core.special_approach import in_interaction_range, interaction_standpoint, ApproachProgress
from utils.core.tool_repair import ToolRepairCycle
from utils.core.coordinate_recovery import RecoveryProgress, validate_recovery_options
from utils.core.coordinate_route import build_waypoints, direction_keys, distance, key_vector, make_basis, rotate_basis, valid_position, validate_coordinate_route
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.timeline_hotkeys import PlaybackControls
from utils.drivers.clock import PreciseClock
from utils.flows.playback_signals import _Paused, _Stopped, _CalibrationRetry, _RecoveryLimit, _SkipPoint
from utils.flows.playback_collection import CollectionActions, SPECIAL_REACHED_TEXT
from utils.flows.playback_recovery import RecoveryActions
from utils.flows.playback_sustain import SustainActions
from utils.flows.playback_visits import VisitActions
from utils.core.visit_progress import MotionProgress


class _Follower(CollectionActions, RecoveryActions, SustainActions, VisitActions):
    """Single owner of input guards, clock and pause/resume progress for all roles."""

    def __init__(self, data, reader, device, window, controls, report, clock, waypoints=None, start_mode='first',
                 recovery_attempts=0, jump_on_recovery=False, collect_wait_seconds=None, loop_count=1,
                 continuous_recovery=False, navigator=None, repair_box=None, tool_selector=None, food=None,
                 scene_refresh=None, stable_navigation=False, skip_unreachable=False):
        self.data, self.reader, self.device = data, reader, device
        self.window, self.controls, self._report, self.clock = window, controls, report, clock
        self.cycle, self.loop_count = 1, loop_count
        self.keys = set()
        self.position, self.position_at = None, None
        self.motion_position, self.motion_at = None, None
        self.active_time, self.last_tick = 0., None
        self.budget = data['duration'] * policy.COORD_PLAYBACK_TIME_FACTOR + policy.COORD_PLAYBACK_EXTRA_SECONDS
        self.waypoints = waypoints
        self.start_mode, self.start_index = start_mode, 0
        if waypoints is not None:
            self.budget = policy.COORD_POINT_PLAYBACK_SECONDS
            if collect_wait_seconds is not None:
                self.budget += sum((p.get('collect_wait', collect_wait_seconds)
                                    + policy.COORD_COLLECTION_PRESS_SECONDS)*p.get('collect_count', 1)
                                   for p in waypoints)
        self.last_report = float('-inf')
        self.recovery_attempts, self.jump_on_recovery = recovery_attempts, jump_on_recovery
        self.recovery = RecoveryProgress()
        self.continuous_recovery = continuous_recovery
        self.resume_from_current = False
        self.collect_wait_seconds, self.collection = collect_wait_seconds, None
        self.last_collect_report = float('-inf')
        self.navigator = navigator
        self.navigation_waiting = False
        self.calibration_speed = 3.
        self.camera_direction = callable(getattr(reader, 'movement_basis', None))
        self.direction_quiet_until = float('-inf')
        self.repair_box = repair_box
        self.tool_selector = tool_selector
        self.equip_pending, self.equip_menu_pending = False, False
        self.equip_verify_until = 0.
        self.repair_cycle = ToolRepairCycle()
        self.last_tool_check = self.last_repair_report = float('-inf')
        self.tool_status = {'state': 'unavailable'}
        self.food = food
        self.stamina_status = {'state': 'unavailable'}
        self.last_stamina_check = float('-inf')
        self.meal_pending = self.meal_menu_pending = False
        self.meal_before = self.meal_deadline = 0.
        self.meal_settle_until = 0.
        self.meal_refilling, self.food_failures, self.food_retry_at = False, 0, 0.
        self.food_menu_checked = False
        self.repair_menu_pending = False
        self.special_progress = None
        self.scene_refresh = scene_refresh
        self.stable_navigation = stable_navigation
        self.skip_unreachable = skip_unreachable and navigator is not None and waypoints is not None
        self.visit_progress = None
        self.retry_pass, self.deferred, self.failed_reasons = 0, [], {}
        self.unresolved_points = []
        self.visit_point = None
        self.motion_progress = MotionProgress()

    def report(self, state, message, **extra):
        if self.waypoints is not None:
            message = f'第 {self.cycle}/{self.loop_count} 轮 · {message}'
            extra.update(cycle=self.cycle, loop_count=self.loop_count)
        if self.skip_unreachable and self.visit_point is not None:
            extra.setdefault('point_id', self.visit_point['id'])
            extra.setdefault('retry_pass', self.retry_pass)
        self._report(state, message, **extra)

    def guard(self):
        running, stopped = self.controls.snapshot()
        if stopped:
            raise _Stopped()
        if running and (not self.window.foreground() or self.window.size() != self.data['client_size']):
            self.controls.pause()
            running = False
        if not running:
            raise _Paused()
        now = self.clock.monotonic()
        if self.last_tick is not None and not self.navigation_waiting:
            self.active_time += max(0., now - self.last_tick)
        self.last_tick = now
        if self.active_time > self.budget:
            raise ValueError('回放超过时限，已停止，请缩短路线后重试')

    def read(self, force=False):
        self.guard()
        started = self.clock.monotonic()
        if not force and self.position_at is not None and started - self.position_at < policy.COORD_SAMPLE_SECONDS:
            return self.position
        read_position = getattr(self.reader, 'read_for_playback', self.reader.read)
        position = read_position()
        now = self.clock.monotonic()
        if not valid_position(position):
            raise ValueError('角色坐标无效，已停止回放')
        if self.position_at is not None:
            # Forced reads can straddle a game-frame update only milliseconds
            # apart. Compare against a retained window, not each forced read.
            # Include read latency: acquisition occurs between started and now.
            dt = max(now - self.motion_at, policy.COORD_SAMPLE_SECONDS)
            movement = math.dist(position, self.motion_position)
            allowed = policy.COORD_MAX_SPEED * dt + policy.COORD_POSITION_SLACK
            if movement > allowed:
                raise ValueError(f'角色坐标发生跳变（位移 {movement:.2f} / 允许 {allowed:.2f}'
                                 f'，采样窗口 {dt:.3f} 秒），已停止回放')
        if self.position_at is None or started - self.motion_at >= policy.COORD_SAMPLE_SECONDS:
            self.motion_position, self.motion_at = tuple(position), started
        self.position, self.position_at = tuple(position), now
        return self.position

    def set_keys(self, keys):
        for key in sorted(self.keys - keys):
            self.device.execute({'type': 'key_up', 'key': key})
            self.keys.discard(key)
        for key in sorted(keys - self.keys):
            self.guard()  # Also check between two keys of a diagonal command.
            self.device.execute({'type': 'key_down', 'key': key})
            self.keys.add(key)

    def release(self):
        self.device.release_all()
        self.keys.clear()

    def wait(self, duration):
        end = self.clock.monotonic() + duration
        while self.clock.monotonic() < end:
            self.read()
            self.clock.sleep(min(.01, max(0., end - self.clock.monotonic())))
        self.read(force=True)

    def pulse(self, key):
        before = self.read(force=True)
        self.set_keys({key})
        try:
            self.wait(policy.COORD_CALIBRATION_SECONDS)
        finally:
            self.release()
        self.wait(policy.COORD_SETTLE_SECONDS)
        after = self.read(force=True)
        return [b - a for a, b in zip(before, after)]

    def calibrate(self, reference, target_index=0):
        position = self.read(force=True)
        if distance(position, reference) > policy.COORD_MAX_START_DISTANCE \
                or abs(position[1] - reference[1]) > policy.COORD_MAX_HEIGHT_ERROR:
            raise ValueError('当前位置离起点或暂停点过远，请回到原位置后重新回放')
        extra = {'start_waypoint': self.start_index + 1, 'waypoint': target_index + 1,
                 'waypoint_count': len(self.waypoints)} if self.waypoints is not None else {}
        message = f'方向校准中 · 目标点位 {target_index + 1}/{len(self.waypoints)} · F9停止' \
            if self.waypoints is not None else '方向校准中：短距离前后、左右走动 · F9停止'
        self.report('calibrating', message, position=list(position), **extra)
        forward, backward = self.pulse('w'), self.pulse('s')
        right, left = self.pulse('d'), self.pulse('a')
        if self.waypoints is not None and self.navigator:
            # At a trunk/wall one direction can be blocked. Its reverse still
            # measures that axis. A blocked pulse must not invalidate the other
            # three valid probes or force a recorded-point start position.
            axes = []
            lengths = []
            for first, reverse in ((forward, backward), (right, left)):
                fl, rl = math.hypot(first[0], first[2]), math.hypot(reverse[0], reverse[2])
                if max(fl, rl) > policy.COORD_MAX_CALIBRATION_DISTANCE \
                        or max(fl, rl) < policy.COORD_MIN_CALIBRATION:
                    raise _CalibrationRetry('方向校准没有读到有效位移')
                if min(fl, rl) >= policy.COORD_MIN_CALIBRATION \
                        and (first[0] * reverse[0] + first[2] * reverse[2]) / (fl * rl) > policy.COORD_REVERSE_COSINE:
                    raise _CalibrationRetry('方向校准期间移动方向改变')
                axes.append(first if fl >= rl else [-v for v in reverse])
                lengths.append(max(fl, rl))
            try:
                basis = make_basis(*axes)
            except ValueError as exc:
                raise _CalibrationRetry(str(exc)) from exc
            self.calibration_speed = max(lengths) / policy.COORD_CALIBRATION_SECONDS
            self.report('playing', '方向校准完成，从当前位置继续寻路', position=list(self.position), **extra)
            return basis
        basis = make_basis(forward, right)
        for first, reverse in ((forward, backward), (right, left)):
            fl, rl = math.hypot(first[0], first[2]), math.hypot(reverse[0], reverse[2])
            if rl < policy.COORD_MIN_CALIBRATION or rl > policy.COORD_MAX_CALIBRATION_DISTANCE \
                    or (first[0] * reverse[0] + first[2] * reverse[2]) / (fl * rl) > policy.COORD_REVERSE_COSINE \
                    or math.hypot(first[0] + reverse[0], first[2] + reverse[2]) > max(
                        policy.COORD_RETURN_ERROR, fl * policy.COORD_RETURN_FRACTION):
                raise ValueError('方向校准的往返位移不一致，请在空旷平地重试')
        if distance(self.position, reference) > policy.COORD_MAX_START_DISTANCE:
            raise ValueError('方向校准偏离起点，请回到起点重试')
        self.report('playing', '正在按坐标回放 · F6暂停 · F9停止', position=list(self.position))
        return basis

    def follow(self):
        points = self.waypoints if self.waypoints is not None else build_waypoints(self.data['samples'])
        index, basis = 0, None
        selected = self.waypoints is None
        dwell = None
        # Manual points may begin far away. Calibrate at the current position,
        # then visit the selected start and subsequent points without decimation.
        reference = points[0]['position'] if self.waypoints is None else None
        anchor, anchor_keys, anchor_at = None, set(), 0.
        best, progress_at = math.inf, self.clock.monotonic()
        segment_start = None
        navigation_leg = None
        navigation_wait_since = None
        special_load_since = None
        special_stalls = 0
        approach_progress = ApproachProgress()
        while index < len(points):
            try:
                if not selected and self.waypoints is not None:
                    reference = self.read(force=True)
                    index = choose_start_index(points, reference, self.start_mode)
                    self.start_index, selected = index, True
                self.visit_point = points[index]
                self.guard()
                self.check_visit(points[index])
                if not self.prepare_mineral_point(points[index], index, len(points)):
                    anchor = None
                    self.motion_progress.reset()
                    continue
                special_near = False
                if 'special_resource' in points[index]:
                    scene = self.navigator.scene_provider() or {}
                    live_objects = self.special_objects(points[index], scene=scene)
                    if live_objects is None or not live_objects and scene.get('global_special_state') != 'ready':
                        self.release()
                        special_load_since = None
                        if self.scene_refresh is not None:
                            self.scene_refresh()
                        if self.clock.monotonic() - self.last_report >= .5:
                            self.report('navigating', '动态场景更新中，已释放按键，读取恢复后继续导航 · F9停止',
                                        waypoint=index+1, waypoint_count=len(points))
                            self.last_report = self.clock.monotonic()
                        self.wait(.25)
                        continue
                    live_target = self.special_target(points[index], allow_global=True, scene=scene)
                    current = self.read(force=True)
                    special_near = in_interaction_range(current, live_target['position'])
                    if special_near and live_target.get('resource_global_only'):
                        self.release()
                        if special_load_since is None:
                            special_load_since = self.clock.monotonic()
                        if self.clock.monotonic()-special_load_since >= policy.COORD_SPECIAL_LOAD_WAIT_SECONDS:
                            from utils.core.special_approach import SpecialTargetUnavailable
                            raise SpecialTargetUnavailable('已到达全图资源位置，但附近模型和交互状态仍未加载，未按 F；请核对资源是否仍在此处')
                        if self.scene_refresh is not None:
                            self.scene_refresh()
                        if self.clock.monotonic() - self.last_report >= .5:
                            self.report('navigating', '已到全图资源位置，等待附近对象与交互状态加载 · F9停止',
                                        waypoint=index+1, waypoint_count=len(points))
                            self.last_report = self.clock.monotonic()
                        self.wait(.2)
                        continue
                    special_load_since = None
                    if special_near:
                        points[index]['position'] = list(current)
                        special_stalls = 0
                        approach_progress.reset()
                    elif (('_approach_for' in points[index] and
                           distance(current, points[index]['position']) <= policy.COORD_SPECIAL_APPROACH_PRECISION)
                          or approach_progress.stalled(current, live_target['position'], self.clock.monotonic())):
                        # Blocked inside the last stretch: re-pick the ring point
                        # instead of sliding against the model for seconds.
                        self.release()
                        avoided = points[index].setdefault('_avoid_standpoints', [])
                        if valid_position(points[index].get('position')) \
                                and all(distance(points[index]['position'], seen) > .01 for seen in avoided):
                            avoided.append(list(points[index]['position']))
                        points[index].pop('_approach_for', None)
                        approach_progress.reset()
                        special_stalls += 1
                        if special_stalls >= policy.COORD_SPECIAL_APPROACH_STALL_LIMIT:
                            self.pause_blocked(index, len(points), '资源附近找不到能站住的落脚点')
                        if self.navigator:
                            self.navigator.invalidate()
                        if self.clock.monotonic() - self.last_report >= .5:
                            self.report('navigating',
                                        f'点位 {index + 1}/{len(points)} · 贴近资源时被挡住，换落脚点 {special_stalls}/'
                                        f'{policy.COORD_SPECIAL_APPROACH_STALL_LIMIT} · F9停止',
                                        waypoint=index+1, waypoint_count=len(points),
                                        special_stall=special_stalls)
                            self.last_report = self.clock.monotonic()
                        self.wait(.15)
                        continue
                    elif '_approach_for' not in points[index] or distance(
                            points[index]['_approach_for'], live_target['position']) > .3 \
                            or not in_interaction_range(points[index]['position'], live_target['position']):
                        self.release()
                        scene = self.navigator.scene_provider()
                        obstacles = ObstacleMap(scene, live_target['position'], goal=live_target['position'])
                        try:
                            approach = interaction_standpoint(current, live_target['position'], obstacles,
                                                              avoid=points[index].get('_avoid_standpoints', ()))
                        except ValueError as exc:
                            self.report('navigating', f'{exc}，等待场景更新 · F9停止',
                                        waypoint=index+1, waypoint_count=len(points))
                            self.wait(.2)
                            continue
                        self.navigator.invalidate()
                        points[index]['position'] = approach
                        points[index]['_approach_for'] = list(live_target['position'])
                if self.maintain_stamina(index, len(points)) or self.maintain_tool(index, len(points)):
                    self.motion_progress.reset()
                    points[index].pop('_mineral_progress', None)
                    self.pause_visit_clock()
                    anchor, navigation_leg, navigation_wait_since = None, None, None
                    best, progress_at = math.inf, self.clock.monotonic()
                    if self.navigator:
                        self.navigator.invalidate()
                    continue
                if self.camera_direction:
                    try:
                        basis = self.reader.movement_basis()
                    except (ValueError, OSError) as exc:
                        self.release()
                        self.motion_progress.reset()
                        if self.clock.monotonic()-self.last_report >= .5:
                            self.report('navigating', f'等待当前镜头方向：{exc} · F6暂停 · F9停止',
                                        waypoint=index+1, waypoint_count=len(points), navigation_path=[])
                            self.last_report = self.clock.monotonic()
                        self.wait(.1)
                        continue
                    if segment_start is None:
                        segment_start = self.position
                if basis is None and self.collection is None and not special_near \
                        and not points[index].get('_mineral_arrival'):
                    # A paused period is outside the coordinate jump/time budget.
                    self.position_at = None
                    if not selected:
                        reference = self.read(force=True)
                        index = choose_start_index(points, reference, self.start_mode)
                        self.start_index, selected = index, True
                    if reference is None:
                        reference = self.read(force=True)
                    if self.resume_from_current:
                        reference = self.read(force=True)
                        self.resume_from_current = False
                        # Manual movement can prove clearance below. F6 alone
                        # must not discard this obstacle's failure history.
                        segment_start = reference
                    try:
                        basis = self.calibrate(reference, target_index=index)
                    except _CalibrationRetry as exc:
                        self.release()
                        reference, basis, anchor = self.position, None, None
                        if self.navigator:
                            self.navigator.invalidate()
                        self.report('calibrating', f'点位 {index + 1}/{len(points)} · {exc}，稍后重新校准 · F9停止',
                                    waypoint=index + 1, waypoint_count=len(points), navigation_path=[])
                        scene_ready = (self.navigator.scene_provider() or {}).get('state') == 'ready'
                        if self.jump_on_recovery and scene_ready and not self.stable_navigation:
                            try:
                                self.set_keys({'space'})
                                self.wait(policy.COORD_RECOVERY_JUMP_SECONDS)
                            finally:
                                self.release()
                            self.wait(policy.COORD_RECOVERY_JUMP_SETTLE_SECONDS)
                        else:
                            self.wait(policy.COORD_RECOVERY_RETRY_SECONDS)
                        reference = self.position
                        navigation_leg, navigation_wait_since = None, None
                        best, progress_at = math.inf, self.clock.monotonic()
                        continue
                    if segment_start is None:
                        segment_start = self.position
                    anchor = None
                    self.motion_progress.reset()
                    best, progress_at = math.inf, self.clock.monotonic()
                position = self.read()
                held_keys = set(self.keys)
                target = points[index]['position']
                remaining = distance(position, target)
                precise_resource_approach = (points[index].get('_mineral_aim', False)
                                             and not points[index].get('_mineral_arrival', False))
                arrival_radius = (policy.COORD_SPECIAL_APPROACH_PRECISION if precise_resource_approach
                                  else policy.COORD_REACH_RADIUS)
                if self.collection is not None and self.collection.get('sent') and remaining > policy.COORD_REACH_RADIUS:
                    raise ValueError('采集等待期间离开了当前点位，已停止，请重新开始回放')
                if abs(position[1] - target[1]) > policy.COORD_MAX_HEIGHT_ERROR \
                        and (self.waypoints is None or remaining <= policy.COORD_REACH_RADIUS):
                    if self.navigator and self.continuous_recovery and self.waypoints is not None:
                        self.release()
                        if self.clock.monotonic() - self.last_report >= .5:
                            self.report('navigating', f'点位 {index + 1}/{len(points)} · 已接近，等待落地或目标高度恢复 · F9停止',
                                        waypoint=index + 1, waypoint_count=len(points),
                                        position=list(position), target_height=target[1], navigation_path=[])
                            self.last_report = self.clock.monotonic()
                        self.navigation_waiting = True
                        try:
                            self.wait(.1)
                        finally:
                            self.navigation_waiting = False
                            self.last_tick = self.clock.monotonic()
                        continue
                    raise ValueError('当前位置与路线高度不一致，请回到相同场景和楼层')
                now = self.clock.monotonic()
                if self.waypoints is not None and self.recovery.observe(position, target):
                    self.report('playing', f'点位 {index + 1}/{len(points)} · 已恢复前进，连续失败已清零'
                                ' · F6暂停 · F9停止', waypoint=index + 1, waypoint_count=len(points),
                                recovery_result='cleared', recovery_failures=0,
                                recovery_limit=self.recovery_attempts)
                if remaining <= arrival_radius and (
                        'special_resource' not in points[index] or special_near):
                    self.set_keys(set())
                    if self.navigator and self.collection is None:
                        # Allow inertia and landing to settle before pressing F.
                        arrival = position
                        self.wait(policy.COORD_SETTLE_SECONDS)
                        position = self.read(force=True)
                        if distance(position, target) > arrival_radius \
                                or abs(position[1] - target[1]) > policy.COORD_MAX_HEIGHT_ERROR \
                                or distance(arrival, position) > policy.COORD_IDLE_RADIUS \
                                or abs(position[1] - arrival[1]) > policy.COORD_IDLE_RADIUS:
                            continue
                    if self.navigator and self.navigator.learning_finished is None:
                        self.navigator.learning_finished = self.active_time
                    if self.waypoints is not None and self.collect_wait_seconds is not None:
                        if not self.collect_point(points[index], index, len(points)):
                            continue
                    else:
                        if dwell is None:
                            dwell = points[index]['wait']
                        if dwell > 0:
                            step = min(policy.COORD_SAMPLE_SECONDS, dwell)
                            self.wait(step)
                            dwell = max(0., dwell - step)
                            continue
                    index += 1
                    if self.navigator:
                        self.navigator.arrived(position)
                    if self.waypoints is not None:
                        text = '已完成采集与等待' if self.collect_wait_seconds is not None else '已到达点位'
                        if 'special_resource' in points[index-1]:
                            text = SPECIAL_REACHED_TEXT[(self.special_progress or {}).get('outcome')]
                        if self.navigator and self.navigator.memory_state == 'write_error':
                            text += '（走法记忆暂未保存）'
                        self.report('reached', f'{text} {index}/{len(points)} · F6暂停 · F9停止',
                                    position=list(position), waypoint=index, waypoint_count=len(points))
                    self.collection = None
                    self.special_progress = None
                    special_stalls = 0
                    approach_progress.reset()
                    dwell, anchor = None, None
                    self.motion_progress.reset()
                    self.recovery.reset()
                    if self.navigator:
                        self.navigator.invalidate()
                    navigation_leg = None
                    segment_start = position
                    best, progress_at = math.inf, now
                    if self.skip_unreachable:
                        points, index = self.finish_visit(points, index-1, position)
                        reference = position
                        navigation_wait_since = None
                    elif self.waypoints is not None and index == len(points):
                        self.report('cycle_finished', '本轮点位回放完成 · F6暂停 · F9停止',
                                    waypoint=index, waypoint_count=len(points))
                        if self.cycle < self.loop_count:
                            self.cycle += 1
                            index, self.start_index = 0, 0
                            # Nearest entry is only for the first cycle. Reuse
                            # the feedback basis and walk back to point 1; do not
                            # recalibrate by walking around a collection node.
                            reference = position
                            self.active_time, self.last_tick = 0., self.clock.monotonic()
                            self.last_report = self.last_collect_report = float('-inf')
                    continue
                if self.navigator:
                    planning_at = self.clock.monotonic()
                    self.navigation_waiting = True
                    try:
                        self.navigator.learning_time = self.active_time
                        self.navigator.set_waypoint(index)
                        target = self.navigator.next_target(position, target, check=self.guard, before_plan=self.release)
                    except NavigationWait as exc:
                        self.motion_progress.reset()
                        self.release()
                        if exc.reason == 'no_path' and self.continuous_recovery and self.recovery_attempts:
                            navigation_wait_since = now if navigation_wait_since is None else navigation_wait_since
                            if now - navigation_wait_since >= policy.COORD_NAV_RECOVERY_WAIT_SECONDS:
                                self.navigation_waiting = False
                                self.last_tick = self.clock.monotonic()
                                try:
                                    self.fail_recovery(index, len(points))
                                    if not self.recover(points[index]['position'], basis, segment_start, index, len(points)):
                                        self.retry_recovery(index, len(points))
                                except _RecoveryLimit as limit:
                                    if limit.reason != 'distance':
                                        raise
                                    self.retry_recovery(index, len(points), distance_limit=True)
                                self.navigator.invalidate()
                                navigation_leg, navigation_wait_since, anchor = None, None, None
                                best, progress_at = math.inf, self.clock.monotonic()
                                continue
                        else:
                            navigation_wait_since = None
                        if now - self.last_report >= .5:
                            self.report('navigating', f'点位 {index + 1}/{len(points)} · {exc} · F6暂停 · F9停止',
                                        waypoint=index + 1, waypoint_count=len(points), navigation_path=[])
                            self.last_report = now
                        try:
                            self.wait(.1)
                        finally:
                            self.navigation_waiting = False
                            self.last_tick = self.clock.monotonic()
                        best, progress_at = math.inf, self.clock.monotonic()
                        continue
                    finally:
                        self.navigation_waiting = False
                        self.last_tick = self.clock.monotonic()
                    progress_at += self.clock.monotonic() - planning_at
                    now = self.clock.monotonic()
                    navigation_wait_since = None
                    if navigation_leg != target:
                        navigation_leg = target
                        best, progress_at = math.inf, now
                    # A valid detour may initially move away from the saved
                    # target. Only progress toward the active steering leg counts.
                    remaining = distance(position, target)
                if remaining < best - policy.COORD_STUCK_DISTANCE:
                    best, progress_at = remaining, now
                physical_stall = self.navigator is not None and self.motion_progress.stalled(position, self.active_time)
                if physical_stall or now - progress_at >= policy.COORD_STUCK_SECONDS:
                    self.motion_progress.reset()
                    if self.waypoints is None:
                        raise ValueError('角色未向目标前进，可能遇到障碍，已停止')
                    if self.navigator:
                        planned_path = [list(p) for p in self.navigator.path]
                        evidence = self.navigator.blocked(position, target)
                        self.report('navigating', '当前路段未取得位移，记录现场碰撞后重新规划 · F9停止',
                                    waypoint=index+1, waypoint_count=len(points), position=list(position),
                                    navigation_path=planned_path, navigation_event='blocked',
                                    obstruction=evidence)
                    try:
                        improved = False
                        # A small advance followed by another stall (or an
                        # interrupted action) remains the same failed episode.
                        self.fail_recovery(index, len(points))
                        while self.recovery.failures < self.recovery_attempts:
                            if self.recover(points[index]['position'], basis, segment_start, index, len(points)):
                                improved = True
                                break
                            self.fail_recovery(index, len(points))
                            if self.navigator:
                                break  # Replan after one local probe instead of repeating the same approach.
                    except _RecoveryLimit as exc:
                        if self.continuous_recovery and exc.reason == 'distance':
                            self.retry_recovery(index, len(points), distance_limit=True)
                            anchor = None
                            best, progress_at = distance(self.position, target), self.clock.monotonic()
                            continue
                        reason = '脱困移动达到高度限制' if exc.reason == 'height' else '脱困移动达到距离限制'
                        self.pause_blocked(index, len(points), reason)
                    if not improved:
                        if self.navigator:
                            self.release()
                            if self.continuous_recovery:
                                self.retry_recovery(index, len(points))
                            self.report('navigating', f'点位 {index + 1}/{len(points)} · 绕行未通过，重新寻路 · F6暂停 · F9停止',
                                        waypoint=index + 1, waypoint_count=len(points), navigation_path=[])
                            navigation_leg = None
                            best, progress_at = math.inf, self.clock.monotonic()
                            continue
                        if self.continuous_recovery:
                            self.retry_recovery(index, len(points))
                            anchor = None
                            best, progress_at = distance(self.position, target), self.clock.monotonic()
                            continue
                        reason = '自动脱困已关闭，角色未向目标前进' if not self.recovery_attempts else '连续失败达到上限'
                        self.pause_blocked(index, len(points), reason)
                    self.report('playing', f'尝试继续前往点位 {index + 1}/{len(points)}'
                                f' · 连续失败 {self.recovery.failures}/{self.recovery_attempts} · F6暂停 · F9停止',
                                waypoint=index + 1, waypoint_count=len(points),
                                recovery_attempt=self.recovery.attempts, recovery_limit=self.recovery_attempts,
                                recovery_failures=self.recovery.failures)
                    anchor = None
                    best, progress_at = distance(self.position, target), self.clock.monotonic()
                    continue
                # XYZ alone cannot distinguish wall projection from camera
                # rotation. For manual point routes keep the validated basis;
                # collision feedback belongs to progress/recovery, not learning
                # a new direction from a wall slide. F6 resume recalibrates.
                if self.waypoints is None and anchor is not None and anchor_keys == self.keys \
                        and now - anchor_at >= policy.COORD_CALIBRATION_SECONDS:
                    displacement = [b - a for a, b in zip(anchor, position)]
                    basis = rotate_basis(basis, self.keys, displacement)
                    anchor, anchor_at = position, now
                elif self.navigator and not self.camera_direction and anchor is not None and anchor_keys == held_keys \
                        and held_keys and now - anchor_at >= policy.COORD_CALIBRATION_SECONDS:
                    displacement = [b - a for a, b in zip(anchor, position)]
                    length = math.hypot(displacement[0], displacement[2])
                    vector = key_vector(held_keys, basis)
                    norm = math.hypot(*vector)
                    projected = (anchor[0] + vector[0] / norm * length, anchor[1],
                                 anchor[2] + vector[1] / norm * length)
                    # Both corridors must be clear: wall-sliding is not evidence
                    # of camera rotation. Large verified changes recalibrate here.
                    if now >= self.direction_quiet_until \
                            and abs(position[1] - anchor[1]) <= policy.COORD_DIRECTION_MAX_VERTICAL_DELTA \
                            and length >= policy.COORD_MIN_CALIBRATION and self.navigator.motion_clear(anchor, position) \
                            and self.navigator.motion_clear(anchor, projected):
                        try:
                            basis = rotate_basis(basis, held_keys, displacement)
                        except ValueError:
                            self.release()
                            self.navigator.invalidate()
                            reference, basis, navigation_leg, anchor = position, None, None, None
                            self.report('calibrating', '移动方向改变，重新校准后继续当前点位',
                                        waypoint=index + 1, waypoint_count=len(points), navigation_path=[])
                            continue
                    anchor, anchor_at = position, now
                special_steering = ({'reach_radius': policy.COORD_SPECIAL_APPROACH_PRECISION}
                                    if 'special_resource' in points[index] or precise_resource_approach else {})
                desired = direction_keys(position, target, basis, **special_steering)
                if self.navigator:
                    steering_travel = max(.15, self.calibration_speed * policy.COORD_SAMPLE_SECONDS * 2)
                    if special_steering:
                        steering_travel = min(steering_travel, max(.02, distance(position, target)))
                    desired = self.navigator.steering_keys(position, target, basis,
                        steering_travel, **special_steering)
                if anchor is None or desired != anchor_keys:
                    anchor, anchor_keys, anchor_at = position, set(desired), now
                self.set_keys(desired)
                if now - self.last_report >= .5:
                    navigation = ({'navigation_path': [list(p) for p in self.navigator.path]} if self.navigator else {})
                    mode = '自动寻路' if self.navigator else '坐标回放'
                    if self.navigator and self.navigator.memory_state == 'reusing':
                        mode = '复用已走路线'
                    elif self.navigator and self.navigator.path_memory:
                        mode = '自动寻路 · 记忆走法'
                    self.report('playing', f'{mode} {index + 1}/{len(points)} · F6暂停 · F9停止',
                                position=list(position), waypoint=index + 1, waypoint_count=len(points), **navigation)
                    self.last_report = now
                if self.navigator and distance(target, points[index]['position']) < .01 \
                        and remaining <= policy.COORD_NAV_APPROACH_RADIUS:
                    self.clock.sleep(min(policy.COORD_SAMPLE_SECONDS,
                                         max(.01, remaining / (2 * self.calibration_speed))))
                    self.release()
                    self.wait(policy.COORD_NAV_APPROACH_SETTLE_SECONDS)
                else:
                    self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
            except _SkipPoint as exc:
                self.motion_progress.reset()
                self.release()
                points, index = self.finish_visit(points, index, self.position, reason=str(exc))
                self.collection = self.special_progress = None
                self.recovery.reset()
                if self.navigator:
                    self.navigator.invalidate()
                segment_start = reference = self.position
                navigation_leg = navigation_wait_since = anchor = dwell = None
                best, progress_at = math.inf, self.clock.monotonic()
            except _Paused:
                self.motion_progress.reset()
                points[index].pop('_mineral_progress', None)
                special_load_since = None
                self.release()
                if self.position is not None:
                    reference = self.position
                self.last_tick, self.position_at, basis = None, None, None
                self.last_tool_check = float('-inf')
                self.last_stamina_check = float('-inf')
                self.food_menu_checked = False
                if self.navigator:
                    self.navigator.invalidate()
                    navigation_leg = None
                    navigation_wait_since = None
                self.clock.sleep(.01)
                self.pause_visit_clock()
        return True


def drive_route(data, reader_factory, device, window, controls, report, clock=PreciseClock):
    reader = None
    try:
        errors = validate_coordinate_route(data)
        if errors:
            raise ValueError('；'.join(errors))
        # Do not open the process until F6 has selected the actual game window.
        while True:
            running, stopped = controls.snapshot()
            if stopped:
                return False
            if running:
                break
            clock.sleep(.01)
        reader = reader_factory()
        return _Follower(data, reader, device, window, controls, report, clock).follow()
    except _Stopped:
        return False
    finally:
        try:
            device.release_all()
        finally:
            if reader:
                reader.close()

def drive_points(data, size, reader_factory, device, window, controls, report, clock=PreciseClock, start_mode='first',
                 recovery_attempts=0, jump_on_recovery=False, collect_wait_seconds=None,
                 loop_count=policy.COORD_PLAYBACK_DEFAULT_LOOPS, continuous_recovery=False, navigator=None,
                 repair_box=None, tool_selector=None, food=None, scene_refresh=None,
                 stable_navigation=False, skip_unreachable=False):
    if 'special_patrol' in data:
        from utils.flows.special_patrol import drive_special_patrol
        return drive_special_patrol(data, size, reader_factory, device, window, controls, report,
            clock=clock, start_mode=start_mode, recovery_attempts=recovery_attempts,
            jump_on_recovery=jump_on_recovery, collect_wait_seconds=collect_wait_seconds,
            loop_count=loop_count, continuous_recovery=continuous_recovery, navigator=navigator,
            repair_box=repair_box, tool_selector=tool_selector, food=food, scene_refresh=scene_refresh,
            point_runner=drive_points, **({'stable_navigation': True} if stable_navigation else {}))
    reader = None
    try:
        points = ordered_waypoints(data)
        if navigator is None and any(p.get('source', {}).get('kind') == 'rare_wood' for p in points):
            raise ValueError('珍稀木材路线需要开启自动寻路，以确认实时树木实体。')
        validate_loop_count(loop_count)
        validate_recovery_options(recovery_attempts, jump_on_recovery)
        if type(stable_navigation) is not bool or type(skip_unreachable) is not bool:
            raise ValueError('寻路稳定策略或跳过点位选项无效。')
        if type(continuous_recovery) is not bool or continuous_recovery and not recovery_attempts:
            raise ValueError('自动重试需要选择 1～10 次的每轮脱困次数。')
        if collect_wait_seconds is not None:
            validate_collect_wait(collect_wait_seconds)
        if start_mode not in ('first', 'nearest'):
            raise ValueError('起点模式无效。')
        if not isinstance(size, (list, tuple)) or len(size) != 2 \
                or any(type(value) is not int or not 64 <= value <= 16384 for value in size):
            raise ValueError('游戏窗口尺寸无效。')
        while True:
            running, stopped = controls.snapshot()
            if stopped:
                return False
            if running:
                break
            clock.sleep(.01)
        reader = reader_factory()
        config = {'duration': 0., 'client_size': list(size), 'patrol_probe': data.get('patrol_probe') is True}
        return _Follower(config, reader, device, window, controls, report, clock,
                         waypoints=points, start_mode=start_mode, recovery_attempts=recovery_attempts,
                         jump_on_recovery=jump_on_recovery, collect_wait_seconds=collect_wait_seconds,
                         loop_count=loop_count, continuous_recovery=continuous_recovery, navigator=navigator,
                         repair_box=repair_box, tool_selector=tool_selector, food=food,
                         scene_refresh=scene_refresh, stable_navigation=stable_navigation,
                         skip_unreachable=skip_unreachable).follow()
    except _Stopped:
        return False
    finally:
        try:
            device.release_all()
        finally:
            if reader:
                reader.close()

def play_main(base, data):
    from utils.drivers.town_window import TimelineInput, TownWindow
    from utils.ui.recorder_feedback import Feedback
    base = Path(base)
    feedback, controls = Feedback(base), None
    try:
        window = TownWindow(data['target_title'])
        controls = PlaybackControls(window, data['client_size'], feedback,
                                    lambda: (base / 'logs/stop-request').exists())
        controls.start()
        feedback.update('playback_wait', '坐标回放已就绪 · 回到起点，周围留空地 · 游戏内按F6校准并开始', window=window)
        completed = drive_route(data, lambda: ReadOnlyPosition.from_window(window, base / 'coordinate-profile.json'),
                                TimelineInput(window), window, controls,
                                lambda state, message, **extra: feedback.update(state, message, window=window, **extra))
        feedback.update('finished' if completed else 'stopped',
                        '坐标路线回放完成' if completed else '回放已停止，按键已释放', window=window)
    except Exception as exc:
        feedback.update('error', f'坐标回放停止：{exc}')
        raise
    finally:
        if controls:
            controls.close()
        feedback.close()
