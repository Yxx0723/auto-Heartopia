"""Position feedback playback, with bounded calibration and injectable devices."""
import math
from pathlib import Path

from utils.core import thresholds as policy
from utils.core.food_policy import FALLBACK_MODES, confirm_inventory
from utils.core.coordinate_points import (choose_start_index, ordered_waypoints, validate_collect_wait,
                                          validate_loop_count)
from utils.core.coordinate_navigation import NavigationWait, ObstacleMap
from utils.core.direction_evidence import CalibrationBudget, DirectionEvidence
from utils.core.navigation_progress import NavigationProgress
from utils.core.special_approach import in_interaction_range, interaction_standpoint
from utils.core.harvest_evidence import HarvestEvidence
from utils.core import special_verdict
from utils.core.special_patrol import SpecialTargetUnavailable
from utils.core.special_verdict import snapshot as special_snapshot
from utils.core.tool_repair import ToolRepairCycle
from utils.core.stamina import usable_stamina
from utils.core.coordinate_recovery import (RecoveryProgress, recovery_plan, recovery_limit_reason,
                                             validate_recovery_options)
from utils.core.coordinate_route import (build_waypoints, direction_keys, distance,
                                         key_vector, make_basis, rotate_basis, valid_position,
                                         validate_coordinate_route)
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.timeline_hotkeys import PlaybackControls
from utils.drivers.town_window import TimelineInput, TownWindow
from utils.drivers.windows_input import PreciseClock
from utils.ui.recorder_feedback import Feedback


class _Paused(Exception):
    pass


class _Stopped(Exception):
    pass


class _CalibrationRetry(Exception):
    pass


class _SkipPoint(Exception):
    pass


class _RecoveryLimit(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


SPECIAL_OUTCOME_TEXT = {
    special_verdict.GATHERED: '资源次数减少，等待背包确认',
    special_verdict.GONE: '资源已消失',
    special_verdict.COOLING: '冷却已开始',
    special_verdict.CHANGED: '状态有变化',
    special_verdict.UNCHANGED: '未见变化',
    special_verdict.UNKNOWN: '未能读到状态',
}

# The per-point line must repeat the verdict, never a bare "presses sent".
SPECIAL_REACHED_TEXT = {
    None: '动态采集已结束',
    special_verdict.GATHERED: '资源次数减少，等待背包确认',
    special_verdict.GONE: '资源已消失，结果未确认',
    special_verdict.COOLING: '冷却已开始，产出未确认',
    special_verdict.CHANGED: '状态有变化，请核对背包',
    special_verdict.UNCHANGED: '未见状态变化，请核对背包',
    special_verdict.UNKNOWN: '状态未能读取，请核对背包',
}


class _Follower:
    def __init__(self, data, reader, device, window, controls, report, clock, waypoints=None, start_mode='first',
                 recovery_attempts=0, jump_on_recovery=False, collect_wait_seconds=None, loop_count=1,
                 continuous_recovery=False, navigator=None, repair_box=None, tool_selector=None, food=None,
                 scene_refresh=None):
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
                self.budget += sum(p.get('collect_count', 1) * (p.get('collect_wait', collect_wait_seconds) + policy.COORD_COLLECTION_PRESS_SECONDS)
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
        self.meal_refilling = False
        self.meal_pending = self.meal_menu_pending = False
        self.meal_before = self.meal_deadline = 0.
        self.meal_settle_until = 0.
        self.food_menu_checked = False
        self.food_retry_at = 0.
        self.food_retry_count = 0
        self.food_fallback = 'fruit'
        self.repair_menu_pending = False
        self.special_progress = None
        self.heading_cache = None  # Ephemeral, one patrol only; never saved as route memory.
        self.direction_method = getattr(reader,'movement_basis',None)
        self.direction_method = self.direction_method if callable(self.direction_method) else None
        self.direction_poll_at = float('-inf')
        self.direction_candidate = self.direction_memory = self.direction_conflict = None
        self.direction_bad_since = None
        self.direction_safe_probe = False
        self.direction_source = 'legacy'
        self.calibration_seconds = policy.COORD_CALIBRATION_SECONDS
        self.scene_refresh = scene_refresh
        self.calibration_budget = CalibrationBudget()
        self.direction_evidence = DirectionEvidence()
        self.skipped_count = 0
        self.skipped_cycles = set()
        self.approach_tag, self.approach_attempts = None, 0
        self.direction_unsafe_until = float('-inf')
        self.travel_progress = NavigationProgress()
        self.travelling = False

    def approach_attempt(self, index):
        tag = self.cycle,index
        if self.approach_tag != tag:
            self.approach_tag, self.approach_attempts = tag, 0
        if self.approach_attempts >= policy.COORD_APPROACH_POINT_ATTEMPTS:
            raise _SkipPoint('不同接近尝试仍未走通，本点本轮尝试结束')
        self.approach_attempts += 1

    def report(self, state, message, **extra):
        extra.setdefault('direction_source', self.direction_source)
        if 'position' not in extra and valid_position(self.position):
            extra.update(position=list(self.position), position_source='last_successful_read',
                         position_age_seconds=max(0., self.clock.monotonic()-self.position_at)
                         if self.position_at is not None else None)
        extra.setdefault('input_keys', sorted(self.keys))
        if self.waypoints is not None:
            message = f'第 {self.cycle}/{self.loop_count} 轮 · {message}'
            extra.update(cycle=self.cycle, loop_count=self.loop_count)
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
            elapsed = max(0., now - self.last_tick)
            self.active_time += elapsed
            if self.travelling and self.navigator and self.waypoints is not None:
                self.travel_progress.account(elapsed)
        self.last_tick = now
        if self.travelling and self.travel_progress.exhausted:
            raise _SkipPoint('本点有效寻路与脱困时间达到预算，暂时跳过并记录未完成')
        if self.active_time > self.budget:
            raise ValueError('回放超过时限，已停止，请缩短路线后重试')

    def read(self, force=False):
        self.guard()
        started = self.clock.monotonic()
        if not force and self.position_at is not None and started - self.position_at < policy.COORD_SAMPLE_SECONDS:
            return self.position
        position = getattr(self.reader, 'read_for_playback', self.reader.read)()
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
        if 'space' in keys:
            discard = getattr(self.navigator, 'discard_learning', None)
            if callable(discard):
                discard()
            self.direction_unsafe_until = self.clock.monotonic()+policy.COORD_RECOVERY_JUMP_SECONDS \
                + policy.COORD_RECOVERY_JUMP_SETTLE_SECONDS
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
            self.wait(self.calibration_seconds)
        finally:
            self.release()
        self.wait(policy.COORD_SETTLE_SECONDS)
        after = self.read(force=True)
        return [b - a for a, b in zip(before, after)]

    @staticmethod
    def basis_angle(first, second):
        a,b=first['forward'],second['forward']
        return math.degrees(math.acos(max(-1.,min(1.,sum(x*y for x,y in zip(a,b))))))

    def read_direction(self):
        now=self.clock.monotonic()
        if now-self.direction_poll_at<policy.COORD_DIRECTION_READ_SECONDS:
            return self.direction_memory
        self.direction_poll_at=now
        try:
            raw=self.direction_method()
            basis=make_basis([raw['forward'][0],0.,raw['forward'][1]],
                             [raw['right'][0],0.,raw['right'][1]])
            if self.direction_conflict and self.basis_angle(basis,self.direction_conflict) \
                    <=policy.COORD_DIRECTION_STABLE_DEGREES:
                self.direction_memory=None
                self.direction_safe_probe=True
                if self.direction_bad_since is None:self.direction_bad_since=now
                return None
            self.direction_conflict=None
        except (OSError,ValueError,TypeError,KeyError,IndexError) as exc:
            self.direction_memory=self.direction_candidate=None
            self.direction_safe_probe=bool(getattr(exc,'safe_to_probe',False))
            if self.direction_bad_since is None:self.direction_bad_since=now
            return None
        previous=self.direction_candidate
        self.direction_candidate=basis
        if previous is None or self.basis_angle(previous,basis)>policy.COORD_DIRECTION_STABLE_DEGREES:
            self.direction_memory=None
            self.direction_safe_probe=False
            return None
        self.direction_memory=basis
        self.direction_bad_since=None
        self.direction_safe_probe=False
        return basis

    def safe_direction_probe(self, position, index):
        """An engineering open-space check, never a claim of grounded/NavMesh data."""
        if self.navigator is None:
            return False
        scene=self.navigator.scene_provider() or {}
        if scene.get('state')!='ready':
            return False
        radius=policy.COORD_DIRECTION_SAFE_RADIUS
        if self.waypoints and 'special_resource' in self.waypoints[index] \
                and distance(position,self.waypoints[index]['position'])<radius:
            return False
        if any(obj.get('category') in ('tree','bush','blueberry','raspberry','rock','ore','fluorite','roaming_oak')
               and valid_position(obj.get('position')) and distance(position,obj['position'])<radius
               for obj in scene.get('objects',())):
            return False
        obstacles=ObstacleMap(scene,position,water_areas=self.navigator.water_areas)
        start=(position[0],position[2])
        return all(obstacles.clear(start,(start[0]+radius*math.cos(i*math.tau/8),
                                          start[1]+radius*math.sin(i*math.tau/8))) for i in range(8))

    def wait_direction(self, index, total):
        self.release()
        if self.clock.monotonic()-self.last_report>=.5:
            self.report('navigating','等待可靠方向数据；未执行走动校准 · F6暂停 · F9停止',
                        waypoint=index+1,waypoint_count=total,navigation_path=[],direction_source='unconfirmed')
            self.last_report=self.clock.monotonic()
        self.navigation_waiting=True
        try:self.wait(policy.COORD_SAMPLE_SECONDS)
        finally:
            self.navigation_waiting=False
            self.last_tick=self.clock.monotonic()

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
            self.calibration_speed = max(lengths) / self.calibration_seconds
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

    def recovery_position(self, segment_start, target, force=False):
        position = self.read(force=force)
        # Continuous traversal may legitimately descend a slope or stairs by
        # more than the old one-unit drop from the obstacle anchor. read()
        # validates 3D motion between samples; arrival still checks the floor.
        reason = recovery_limit_reason(position, self.recovery.origin, segment_start, target,
                                       check_height=not self.continuous_recovery)
        if reason is not None:
            raise _RecoveryLimit(reason)
        return position

    def recovery_jump(self, target, basis, segment_start):
        before = self.recovery_position(segment_start, target, force=True)
        end = self.clock.monotonic() + policy.COORD_RECOVERY_JUMP_SECONDS
        advance = True
        try:
            while self.clock.monotonic() < end:
                position = self.recovery_position(segment_start, target)
                # Hold Space for the full pulse, but stop forward input near
                # the point or at the bounded probe distance to avoid overshoot.
                advance = advance and distance(before, position) < policy.COORD_RECOVERY_FORWARD_DISTANCE \
                    and distance(position, target) > policy.COORD_REACH_RADIUS
                keys = direction_keys(position, target, basis) if advance else set()
                if advance and self.navigator:
                    keys = self.navigator.recovery_keys(position, target, basis,
                        max(.15, self.calibration_speed * policy.COORD_SAMPLE_SECONDS * 2))
                    if keys is None:
                        return False
                self.set_keys(keys | {'space'})
                self.clock.sleep(min(.01, max(0., end - self.clock.monotonic())))
        finally:
            self.release()
        # Read during post-release observation too. A vertical jump alone
        # cannot count as progress because distance() uses the XZ plane.
        end = self.clock.monotonic() + policy.COORD_RECOVERY_JUMP_SETTLE_SECONDS
        while self.clock.monotonic() < end:
            self.recovery_position(segment_start, target)
            self.clock.sleep(min(.01, max(0., end - self.clock.monotonic())))
        position = self.recovery_position(segment_start, target, force=True)
        return distance(position, target) < distance(before, target) - policy.COORD_STUCK_DISTANCE \
            or distance(position, target) <= policy.COORD_REACH_RADIUS

    def recovery_leg(self, target, basis, segment_start, route_target):
        reason = recovery_limit_reason(target, self.recovery.origin, segment_start, route_target,
                                       check_height=not self.continuous_recovery)
        if reason is not None:
            raise _RecoveryLimit(reason)
        end = self.clock.monotonic() + policy.COORD_RECOVERY_LEG_SECONDS
        try:
            while self.clock.monotonic() < end:
                position = self.recovery_position(segment_start, route_target)
                if distance(position, target) <= policy.COORD_REACH_RADIUS:
                    return True
                keys = direction_keys(position, target, basis)
                if self.navigator:
                    keys = self.navigator.recovery_keys(position, target, basis,
                        max(.15, self.calibration_speed * policy.COORD_SAMPLE_SECONDS * 2))
                    if keys is None:
                        return False
                self.set_keys(keys)
                self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
            position = self.recovery_position(segment_start, route_target, force=True)
            return distance(position, target) <= policy.COORD_REACH_RADIUS
        finally:
            self.release()

    def recover(self, target, basis, segment_start, index, total):
        self.release()
        before = self.read(force=True)
        self.recovery.begin(before)
        self.travel_progress.begin_probe(before)
        if self.waypoints is not None:
            # Manual points can be far apart. Quantized WASD steering may take
            # a valid approach outside the original start-to-point corridor.
            # Bound detours around this obstacle, independent of that approach.
            segment_start = self.recovery.origin
        self.report('recovering', f'点位 {index + 1}/{total} · 连续失败 {self.recovery.failures}/{self.recovery_attempts}'
                    ' · 正在处理障碍 · F6暂停 · F9停止', position=list(before),
                    waypoint=index + 1, waypoint_count=total, recovery_phase='start',
                    recovery_attempt=self.recovery.attempts, recovery_limit=self.recovery_attempts,
                    recovery_failures=self.recovery.failures)
        # Escalate an unresolved episode instead of jumping at the same wall
        # on every replan. Legacy non-navigation playback keeps its policy.
        if self.jump_on_recovery and (not self.navigator or not self.continuous_recovery
                                      or self.recovery.attempts == 1):
            self.report('recovering', f'点位 {index + 1}/{total} · 连续失败 {self.recovery.failures}/{self.recovery_attempts}'
                        ' · 大跳：长按空格 '
                        f'{policy.COORD_RECOVERY_JUMP_SECONDS:g} 秒 · F6暂停 · F9停止',
                        position=list(before), waypoint=index + 1, waypoint_count=total,
                        recovery_phase='jump', recovery_attempt=self.recovery.attempts,
                        recovery_limit=self.recovery_attempts, recovery_failures=self.recovery.failures)
            if self.recovery_jump(target, basis, segment_start):
                return True
        self.report('recovering', f'点位 {index + 1}/{total} · 左右试走校正 '
                    f'· 连续失败 {self.recovery.failures}/{self.recovery_attempts} · F6暂停 · F9停止',
                    position=list(self.position), waypoint=index + 1, waypoint_count=total,
                    recovery_phase='detour', recovery_attempt=self.recovery.attempts,
                    recovery_limit=self.recovery_attempts, recovery_failures=self.recovery.failures)
        plan = recovery_plan(self.recovery.origin, target, self.recovery.attempts)
        for point in plan:
            if not self.recovery_leg(point, basis, segment_start, target):
                return False
        self.wait(policy.COORD_SETTLE_SECONDS)
        position = self.read(force=True)
        reason = recovery_limit_reason(position, self.recovery.origin, segment_start, target,
                                       check_height=not self.continuous_recovery)
        if reason is not None:
            raise _RecoveryLimit(reason)
        improved = distance(position, target) < distance(before, target) - policy.COORD_STUCK_DISTANCE
        return improved

    def fail_recovery(self, index, total):
        if self.recovery.fail():
            self.report('recovering', f'点位 {index + 1}/{total} · 本轮未通过'
                        f' · 连续失败 {self.recovery.failures}/{self.recovery_attempts} · F6暂停 · F9停止',
                        waypoint=index + 1, waypoint_count=total, recovery_result='failed',
                        recovery_attempt=self.recovery.attempts, recovery_failures=self.recovery.failures,
                        recovery_limit=self.recovery_attempts)

    def pause_blocked(self, index, total, reason='连续失败达到上限'):
        self.release()
        self.resume_from_current = True
        self.controls.pause()
        self.report('blocked', f'点位 {index + 1}/{total} · {reason}，已暂停'
                    ' · 手动绕过后 F6继续 · F9停止', waypoint=index + 1, waypoint_count=total,
                    recovery_attempt=self.recovery.attempts, recovery_limit=self.recovery_attempts,
                    recovery_failures=self.recovery.failures)
        raise _Paused()

    def retry_recovery(self, index, total, distance_limit=False):
        self.release()
        self.fail_recovery(index, total)
        if distance_limit:
            # End this probe and plan the next one locally. Do not treat the
            # target's distance as a reason to pause or skip the saved point.
            self.recovery.origin = tuple(self.position)
        if self.recovery.failures >= self.recovery_attempts:
            self.recovery.failures, self.recovery.pending = 0, False
        reason = '已收回距离边界处的试探，重新规划' if distance_limit else '本轮未通过，开始下一轮脱困'
        self.report('recovering', f'点位 {index + 1}/{total} · {reason} · 自动继续 · F6暂停 · F9停止',
                    waypoint=index + 1, waypoint_count=total,
                    recovery_result='distance_retry' if distance_limit else 'auto_retry',
                    recovery_attempt=self.recovery.attempts, recovery_failures=self.recovery.failures,
                    recovery_limit=self.recovery_attempts)
        self.wait(policy.COORD_RECOVERY_RETRY_SECONDS)

    def collection_position(self, point):
        position = self.read(force=True)
        if distance(position, point['position']) > policy.COORD_REACH_RADIUS \
                or abs(position[1] - point['position'][1]) > policy.COORD_MAX_HEIGHT_ERROR:
            raise ValueError('采集等待期间离开了当前点位或高度改变，已停止，请重新开始回放')
        return position

    def collect_point(self, point, index, total):
        if 'special_resource' in point:
            return self.collect_special(point, index, total)
        if self.collection is None:
            self.collection = {'sent': 0, 'remaining': point.get('collect_wait', self.collect_wait_seconds)}
        self.release()
        self.collection_position(point)
        count = point.get('collect_count', 1)
        if self.collection['sent'] < count and self.collection.get('ready', True):
            if self.food is not None:
                self.read_stamina()
                if self.maintain_stamina(index, total):
                    return False
            if self.tool_selector is not None:
                self.read_tool()
                if self.maintain_tool(index, total):
                    return False
            self.report('collecting', f'点位 {index + 1}/{total} · 按 F 采集 {self.collection["sent"] + 1}/{count} · F6暂停 · F9停止',
                        waypoint=index + 1, waypoint_count=total)
            try:
                self.set_keys({'f'})
                self.collection['sent'] += 1
                self.collection['ready'] = False
                self.wait(policy.COORD_COLLECTION_PRESS_SECONDS)
            finally:
                self.release()
        remaining = self.collection['remaining']
        now = self.clock.monotonic()
        if now - self.last_collect_report >= policy.COORD_MONITOR_REFRESH_SECONDS:
            self.report('collection_wait', f'点位 {index + 1}/{total} · 采集等待 {remaining:.1f} 秒'
                        ' · F6暂停 · F9停止', waypoint=index + 1, waypoint_count=total,
                        remaining_wait=remaining)
            self.last_collect_report = now
        if remaining > 0:
            step = min(policy.COORD_SAMPLE_SECONDS, remaining)
            before = self.clock.monotonic()
            completed = False
            try:
                self.wait(step)
                completed = True
            finally:
                # Only elapsed active time counts. A pause releases F and
                # resumes this dwell without sending F again or moving to calibrate.
                elapsed = step if completed else min(step, max(0., self.clock.monotonic() - before))
                self.collection['remaining'] = max(0., remaining - elapsed)
        self.collection_position(point)
        if self.collection['remaining'] > 0:
            return False
        if self.collection['sent'] < count:
            self.collection['remaining'] = point.get('collect_wait', self.collect_wait_seconds)
            self.collection['ready'] = True
            return False
        return True

    def special_objects(self, point):
        """Every live object matching this dynamic identity, or None when unreadable."""
        if self.navigator is None:
            raise ValueError('动态资源采集需要连接实时场景与自动寻路')
        scene = self.navigator.scene_provider() or {}
        if scene.get('state') != 'ready':
            return None
        identity = point['special_resource']
        return [obj for obj in scene.get('objects', [])
                if obj.get('category') == identity['category']
                and obj.get('resource_static_id') == identity['static_id']
                and obj.get('map_dynamic') is True and not obj.get('map_cached')
                and valid_position(obj.get('position'))]

    def special_target(self, point):
        targets = self.special_objects(point)
        if targets is None:
            raise SpecialTargetUnavailable('动态资源场景尚未就绪，未按 F')
        if len(targets) != 1 or type(targets[0].get('resource_available')) is not int:
            raise SpecialTargetUnavailable('当前动态资源未加载、已离开视野或存在歧义，未按 F')
        return targets[0]

    def sample_special(self, point):
        """One refreshed read-only sample: target, 'unknown', or None when it is gone."""
        if self.scene_refresh is not None:
            try:
                self.scene_refresh()
            except (OSError, ValueError):
                pass
        targets = self.special_objects(point)
        if targets is None:
            return 'unknown'
        if not targets:
            return None
        if len(targets) != 1 or type(targets[0].get('resource_available')) is not int:
            return 'unknown'
        return targets[0]

    def special_inventory(self):
        method = getattr(self.reader, 'backpack_counts_snapshot', None)
        if not callable(method):
            return None
        try:
            return method()
        except (OSError, ValueError):
            return None  # Unknown is never a zero stock snapshot.

    def observe_special(self, point, target='unknown'):
        progress = self.special_progress
        evidence = progress.get('evidence')
        if evidence is not None:
            evidence.observe(target, self.special_inventory())
            progress['outcome'] = (special_verdict.GATHERED if evidence.fields()['resource_confirmed']
                                   else evidence.counter_outcome)
            progress['after'] = evidence.after

    def settle_special(self, point):
        """Keep the original target/bag baseline across F, repair and pause."""
        progress = self.special_progress
        if progress['before'] is None:
            return
        deadline = self.clock.monotonic() + policy.COORD_SPECIAL_STATE_SETTLE_SECONDS
        while True:
            self.wait(policy.COORD_SPECIAL_STATE_POLL_SECONDS)
            self.observe_special(point, self.sample_special(point))
            if special_verdict.conclusive(progress['outcome']) or self.clock.monotonic() >= deadline:
                break
        # Clear only after settling: F6 during a press/settle retains pending evidence.
        progress['before'] = None
        outcome = progress['outcome']
        progress['unchanged'] = progress['unchanged']+1 if outcome == special_verdict.UNCHANGED else 0
        self.report('collecting', f'{point["name"]} · 第 {progress["sent"]} 次按 F · {SPECIAL_OUTCOME_TEXT[outcome]}',
                    waypoint=progress.get('index', 1), waypoint_count=progress.get('total', 1),
                    resource_press_count=progress['sent'], resource_outcome=outcome,
                    **progress['evidence'].fields())

    def finish_special(self, point, index, total):
        """Report what is actually known about this dynamic resource and end the point."""
        progress = self.special_progress
        sent, outcome = progress['sent'], progress['outcome']
        evidence = progress.get('evidence')
        # Final bounded reconciliation also runs when the entity vanished/cools.
        if evidence is not None and not evidence.fields()['resource_confirmed'] and sent:
            if progress.get('final_deadline') is None:
                progress['final_deadline'] = self.clock.monotonic()+policy.COORD_SPECIAL_INVENTORY_SETTLE_SECONDS
            while self.clock.monotonic() < progress['final_deadline']:
                self.wait(policy.COORD_SPECIAL_STATE_POLL_SECONDS)
                self.observe_special(point, self.sample_special(point))
                if evidence.fields()['resource_confirmed']:
                    break
        outcome = progress['outcome']
        fields = evidence.fields() if evidence else {'resource_confirmed': False}
        name = point['name']
        if fields['resource_confirmed']:
            message = f'{name} · 已确认采到（背包增加 {fields["resource_inventory_delta"]}）· 共按 F {sent} 次'
        elif outcome == special_verdict.GATHERED:
            message = f'{name} · 资源次数已减少，背包增量尚未确认 · 共按 F {sent} 次'
        elif outcome == special_verdict.GONE:
            message = f'{name} · 资源已从场景消失，结果未确认 · 共按 F {sent} 次'
        elif outcome == special_verdict.COOLING:
            message = f'{name} · 冷却已开始，产出未确认 · 共按 F {sent} 次'
        elif outcome == special_verdict.UNCHANGED:
            message = (f'{name} · 连按 F {sent} 次资源状态无变化，已停止 · 请核对背包'
                       '（可能已在冷却或未命中）')
        elif outcome == special_verdict.CHANGED:
            message = f'{name} · 已按 F {sent} 次，资源状态有变化但未见掉落标志 · 请核对背包'
        else:
            message = f'{name} · 已按 F {sent} 次，未能读到资源状态 · 请核对背包'
        self.report('collecting', message, waypoint=index+1, waypoint_count=total,
                    resource_press_count=sent, resource_outcome=outcome,
                    **fields)
        return True

    def collect_special(self, point, index, total):
        self.release()
        limit = point['special_resource'].get('press_count', 10)
        progress = self.special_progress
        if progress is None:
            progress = self.special_progress = {'sent': 0, 'unchanged': 0, 'outcome': None,
                                                'before': None, 'after': None, 'pressed_from': None}
        progress['index'], progress['total'] = index, total
        if progress['sent']:
            self.observe_special(point, self.sample_special(point))
        if progress['before'] is not None:
            self.settle_special(point)
        if (progress.get('evidence') and progress['evidence'].invalid) \
                or special_verdict.conclusive(progress['outcome']) \
                or progress['unchanged'] >= policy.COORD_SPECIAL_UNCHANGED_LIMIT \
                or progress['sent'] >= limit:
            return self.finish_special(point, index, total)
        try:
            target = self.special_target(point)
        except ValueError:
            # A target that vanished after a press is a completed collection,
            # not a broken route. A second read guards against one short scan.
            if progress['sent'] > 0 and self.sample_special(point) is None:
                progress['outcome'] = special_verdict.GONE
                return self.finish_special(point, index, total)
            raise
        if target['resource_available'] <= 0:
            raise SpecialTargetUnavailable('资源当前没有可用采集标志，已释放按键，请核对是否已采完或处于冷却')
        current = self.read(force=True)
        if not in_interaction_range(current, target['position']):
            return False
        if progress.get('evidence') is None:
            progress['evidence'] = HarvestEvidence(point['special_resource']['category'], target, self.special_inventory())
            journal = getattr(self, 'harvest_journal', None)
            if journal is not None:
                journal.append((self.clock.monotonic(), progress['evidence']))
        progress['before'] = special_snapshot(target)
        progress['pressed_from'] = progress['before']['available']
        self.report('collecting', f'{point["name"]} · 连按 F {progress["sent"]+1}/{limit} · F6暂停 · F9停止',
                    waypoint=index+1, waypoint_count=total, resource_press_count=progress['sent'],
                    position=list(self.position), **progress['evidence'].fields())
        try:
            self.set_keys({'f'})
            progress['sent'] += 1
            progress['evidence'].pressed = True
            self.wait(policy.COORD_COLLECTION_PRESS_SECONDS)
        finally:
            self.release()
        self.settle_special(point)
        if (progress.get('evidence') and progress['evidence'].invalid) \
                or special_verdict.conclusive(progress['outcome']) \
                or progress['unchanged'] >= policy.COORD_SPECIAL_UNCHANGED_LIMIT:
            return self.finish_special(point, index, total)
        return False

    def repair_pause(self, message, index, total, state='repair_paused'):
        self.release()
        pause_with_reason = getattr(self.controls, 'pause_with_reason', None)
        if pause_with_reason:
            pause_with_reason(message)
        else:
            self.controls.pause()
        self.report(state, message+' · 处理后切回游戏按 F6 继续 · F9停止',
                    waypoint=index+1, waypoint_count=total)
        raise _Paused()

    def repair_tap(self, key):
        try:
            self.set_keys({key})
            self.wait(.1)
        finally:
            self.release()

    def read_tool(self):
        self.last_tool_check = self.clock.monotonic()
        try:
            self.tool_status = self.reader.tools_snapshot()
        except (ValueError, OSError):
            self.tool_status = {'state': 'unavailable'}
        return self.tool_status

    def read_stamina(self):
        self.last_stamina_check = self.clock.monotonic()
        try:
            self.stamina_status = self.reader.stamina_snapshot()
        except (ValueError, OSError):
            self.stamina_status = {'state': 'unavailable'}
        return self.stamina_status

    def close_stamina_menu(self, index, total):
        self.release()
        self.navigation_waiting = True
        try:
            closed = self.food.close_menu(self.window, self.repair_tap, self.guard, self.wait, self.device)
            self.meal_menu_pending = False
            self.food_menu_checked = True
            if closed:
                self.report('playing', '已关闭背包，继续当前点位 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total)
            return bool(closed)
        except (ValueError, OSError) as exc:
            return self.food_retry(f'关闭背包未完成：{exc}', index, total)
        finally:
            self.release()
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()

    def food_retry(self, reason, index, total):
        self.release()
        self.food_retry_count += 1
        delay = min(policy.COORD_FOOD_RETRY_MAX_SECONDS,
                    policy.COORD_FOOD_RETRY_SECONDS * 2**min(self.food_retry_count-1, 3))
        self.food_retry_at = self.clock.monotonic()+delay
        self.report('eating', f'自动进食等待重试：{reason} · {delay:g}秒后再核对 · F6暂停 · F9停止',
                    waypoint=index+1, waypoint_count=total, food_retry_count=self.food_retry_count)
        return True

    def food_retry_wait(self):
        self.release()
        self.navigation_waiting = True
        try:
            self.wait(min(policy.COORD_FOOD_RETRY_POLL_SECONDS,
                          max(0.,self.food_retry_at-self.clock.monotonic())))
        finally:
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()
        return True

    def maintain_stamina(self, index, total):
        if self.food is None:
            return False
        if self.clock.monotonic()<self.food_retry_at:
            return self.food_retry_wait()
        if self.clock.monotonic()-self.last_stamina_check >= 1.:
            self.read_stamina()
        status = self.stamina_status
        if not usable_stamina(status):
            self.release()
            self.navigation_waiting = True
            try:
                for _ in range(3):
                    self.wait(.1)
                    status = self.read_stamina()
                    if usable_stamina(status):
                        break
            finally:
                self.navigation_waiting = False
                self.last_tick = self.clock.monotonic()
        if not usable_stamina(status):
            return self.food_retry('暂时无法读取本地角色体力，等待读取恢复', index, total)
        if status['current'] < 30:
            self.meal_refilling = True
        if status['current'] >= 30 and not self.meal_refilling and not self.meal_pending and not self.meal_menu_pending:
            # Starting or resuming may leave a backpack from manual eating or
            # an earlier run open. Check once before movement, never per tick.
            if not self.food_menu_checked:
                return self.close_stamina_menu(index, total)
            return False
        self.release()
        self.navigation_waiting = True
        try:
            action = '正在随机进食，补充至100' if status['current'] < 100 else '已恢复，正在收起背包'
            self.report('eating', f'体力 {status["current"]} / {status["maximum"]}，{action} · F6暂停 · F9停止',
                        waypoint=index+1, waypoint_count=total)
            if self.clock.monotonic() < self.meal_settle_until:
                self.wait(self.meal_settle_until-self.clock.monotonic())
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('体力正在更新，请稍后继续')
            if not self.meal_pending and status['current'] < 100:
                self.meal_menu_pending = True
                inventory_reader = getattr(self.reader, 'food_inventory_snapshot', None)
                if callable(inventory_reader):
                    inventory = inventory_reader(check=self.guard)
                    target = self.food.prepare(self.window, self.repair_tap, self.wait, self.guard, self.device,
                                               inventory=inventory,
                                               query_check=getattr(self.reader, 'food_search_query', None),
                                               fallback_mode=self.food_fallback)
                    self.report('eating', f'正在食用{target["food"]}，目标体力100 · F6暂停 · F9停止',
                                waypoint=index+1, waypoint_count=total, food_name=target['food'])
                else:  # Simulation adapters retain their previous contract.
                    target = self.food.prepare(self.window, self.repair_tap, self.wait, self.guard, self.device)
                confirm = getattr(self.food, 'confirm', None)
                if callable(confirm) and callable(inventory_reader):
                    fresh = inventory_reader(check=self.guard)
                    confirm_inventory(target, fresh, self.food_fallback)
                    target = confirm(target, self.window, self.wait, self.guard,
                                     query_check=getattr(self.reader, 'food_search_query', None))
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('食用前无法确认体力')
                if status['current'] < 100:
                    # Retain the attempted use across F6, focus loss and a
                    # partial mouse press. Never eat twice before verifying.
                    self.meal_pending = True
                    self.meal_before, self.meal_deadline = status['current'], self.clock.monotonic()+8.
                    try:
                        self.food.use(target, self.device, self.guard, self.wait)
                    finally:
                        self.release()
            status = self.read_stamina()
            while self.meal_pending and self.clock.monotonic() < self.meal_deadline \
                    and (not usable_stamina(status) or status['current'] <= self.meal_before):
                self.wait(.1)
                status = self.read_stamina()
            if not usable_stamina(status):
                raise ValueError('食用后暂时无法确认体力，请等待恢复后继续')
            if self.meal_pending and status['current'] <= self.meal_before:
                raise ValueError('上次食用结果未确认，等待读数恢复；不会重复点击食用')
            if self.meal_pending:
                self.food_retry_count, self.food_retry_at = 0, 0.
                self.meal_pending = False
                self.meal_settle_until = self.clock.monotonic()+2.
                self.wait(2.)  # Keep the confirmed eating animation out of movement/calibration.
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('体力正在更新，请稍后继续')
            if status['current'] >= 100:
                self.food_retry_count, self.food_retry_at = 0, 0.
                self.meal_refilling = False
                if self.meal_menu_pending:
                    self.food.close_menu(self.window, self.repair_tap, self.guard, self.wait, self.device)
                    self.meal_menu_pending = False
                    self.food_menu_checked = True
                self.report('playing', f'体力已恢复至 {status["current"]}，继续当前点位 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total)
            return True
        except (ValueError, OSError) as exc:
            return self.food_retry(f'自动进食未完成：{exc}', index, total)
        finally:
            self.release()
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()

    def equip_axe_pick(self, index, total):
        self.release()
        self.navigation_waiting = True
        self.report('equipping', '正在拿出斧镐，确认持用后自动继续 · F6暂停 · F9停止',
                    waypoint=index+1, waypoint_count=total)
        try:
            if not self.equip_pending:
                self.wait(.15)
                tool = self.read_tool()
                if not ToolRepairCycle.usable(tool):
                    raise ValueError('暂时无法确认斧镐状态，请等待读取恢复')
                if not tool['equipped']:
                    self.equip_menu_pending = True
                    target = self.tool_selector.prepare(self.window, self.repair_tap, self.wait, self.guard)
                    # Opening the menu can race a manual selection. Clicking an
                    # already held tool can put it away, so re-read before input.
                    tool = self.read_tool()
                    if not ToolRepairCycle.usable(tool):
                        raise ValueError('选用前斧镐状态发生变化')
                    if not tool['equipped']:
                        self.equip_pending = True
                        self.equip_verify_until = self.clock.monotonic()+3.
                        try:
                            self.tool_selector.use(target, self.device, self.guard, self.wait)
                        finally:
                            self.release()
            # An interrupted click is never repeated on F6 resume. First
            # verify its result, even if the original deadline has elapsed.
            tool = self.read_tool()
            while self.equip_pending and self.clock.monotonic() < self.equip_verify_until \
                    and not (ToolRepairCycle.usable(tool) and tool['equipped']):
                self.wait(.1)
                tool = self.read_tool()
            if not ToolRepairCycle.usable(tool) or not tool['equipped']:
                self.equip_pending = False
                raise ValueError('选用后尚未确认手持斧镐，请检查 T 面板')
            if self.equip_menu_pending:
                self.tool_selector.close_menu(self.window, self.repair_tap, self.guard, self.wait)
                self.equip_menu_pending = False
            self.equip_pending = False
            tool = self.read_tool()
            if not ToolRepairCycle.usable(tool) or not tool['equipped']:
                raise ValueError('斧镐持用状态再次改变，请确认后继续')
            self.report('playing', '已手持斧镐，继续当前点位 · F6暂停 · F9停止',
                        waypoint=index+1, waypoint_count=total)
            return True
        except (ValueError, OSError) as exc:
            self.repair_pause(f'自动拿出斧镐未完成：{exc}', index, total, state='equip_paused')
        finally:
            self.release()
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()

    def maintain_tool(self, index, total):
        if self.repair_box is None and self.tool_selector is None:
            return False
        now = self.clock.monotonic()
        if now-self.last_tool_check >= 1.:
            self.read_tool()
        if self.tool_selector is not None:
            if self.tool_status.get('state') == 'locked':
                self.repair_pause('斧镐尚未解锁，暂时无法自动拿出', index, total, state='equip_paused')
            if not ToolRepairCycle.usable(self.tool_status):
                self.repair_pause('暂时无法确认斧镐持用状态，请等待读取恢复', index, total, state='equip_paused')
            if not self.tool_status['equipped'] or self.equip_pending or self.equip_menu_pending:
                return self.equip_axe_pick(index, total)
        if self.repair_box is None:
            return False
        outcome = self.repair_cycle.observe(self.tool_status, now)
        if self.repair_menu_pending and not self.repair_cycle.active:
            try:
                self.repair_box.close_menu(self.window, self.repair_tap, self.guard, self.wait)
                self.repair_menu_pending = False
            except (ValueError, OSError) as exc:
                self.repair_pause(f'关闭 T 面板失败：{exc}', index, total)
        if outcome == 'idle':
            return False
        failures = {'unavailable': '自动维修暂时读不到耐久，请等待游戏恢复',
                    'not_held': '斧镐耐久偏低，请先手持斧镐再自动维修',
                    'changed': '维修期间工具数据改变，请确认正在持用斧镐',
                    'failed': '维修后耐久未恢复到20%，请检查维修盒数量或手动维修'}
        if outcome in failures:
            self.repair_pause(failures[outcome], index, total)
        self.release()
        self.navigation_waiting = True
        try:
            if outcome == 'needed':
                self.report('repairing', '耐久低于20%，正在打开 T 面板使用维修盒 · F9停止',
                            waypoint=index+1, waypoint_count=total, repair_phase='opening')
                try:
                    target = self.repair_box.prepare(self.window, self.repair_tap, self.wait, self.guard)
                    self.repair_menu_pending = True
                    # Preparation takes time. Recheck the actual tool immediately
                    # before consuming anything, and retain the attempt on pause.
                    self.tool_status = self.reader.tools_snapshot()
                    if self.repair_cycle.observe(self.tool_status, self.clock.monotonic()) != 'needed':
                        return True
                    self.repair_cycle.mark_used(self.tool_status, self.clock.monotonic())
                    try:
                        self.repair_box.use(target, self.device, self.guard, self.wait)
                    finally:
                        # Wait a full effect duration AFTER releasing the click,
                        # including when F6 interrupts that press.
                        self.repair_cycle.started_at = self.clock.monotonic()
                except (ValueError, OSError) as exc:
                    self.repair_pause(f'维修盒使用未确认：{exc}', index, total)
                finally:
                    self.release()
            if outcome == 'completed':
                try:
                    self.repair_box.close_menu(self.window, self.repair_tap, self.guard, self.wait)
                    self.repair_menu_pending = False
                except (ValueError, OSError) as exc:
                    self.repair_pause(f'维修已完成，关闭 T 面板失败：{exc}', index, total)
                self.report('playing', '维修完成，继续当前点位 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total, repair_phase='completed')
                return True
            remaining = self.repair_cycle.remaining(self.clock.monotonic())
            if now-self.last_repair_report >= .5:
                self.report('repairing', f'维修盒恢复中 · 剩余 {remaining:.1f} 秒 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total, repair_phase='waiting',
                            repair_remaining=remaining)
                self.last_repair_report = now
            self.wait(min(.1, max(.01, remaining)))
            return True
        finally:
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()

    def follow(self):
        points = self.waypoints if self.waypoints is not None else build_waypoints(self.data['samples'])
        index, basis = 0, None
        if self.heading_cache and 'basis' in self.heading_cache:
            try:
                cached=self.heading_cache['basis']
                basis=make_basis([cached['forward'][0],0.,cached['forward'][1]],
                                 [cached['right'][0],0.,cached['right'][1]])
            except (KeyError,TypeError,ValueError,IndexError):
                self.heading_cache.clear()
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
        while index < len(points):
            try:
                self.guard()
                self.travelling = False
                special_near = False
                if 'special_resource' in points[index]:
                    try:
                        live_target = self.special_target(points[index])
                    except SpecialTargetUnavailable:
                        if not self.special_progress or not self.special_progress['sent']:
                            raise
                        # No more input to an unloaded entity: let collect_special
                        # reconcile the pending bag update through the normal point flow.
                        live_target = {'position': list(self.read(force=True))}
                    current = self.read(force=True)
                    special_near = in_interaction_range(current, live_target['position'])
                    if special_near:
                        points[index]['position'] = list(current)
                    elif '_approach_for' not in points[index] or distance(
                            points[index]['_approach_for'], live_target['position']) > .3 \
                            or not in_interaction_range(points[index]['position'], live_target['position']):
                        self.release()
                        scene = self.navigator.scene_provider()
                        obstacles = ObstacleMap(scene, live_target['position'], goal=live_target['position'])
                        try:
                            approach = interaction_standpoint(current, live_target['position'], obstacles)
                        except ValueError as exc:
                            self.report('navigating', f'{exc}，等待场景更新 · F9停止',
                                        waypoint=index+1, waypoint_count=len(points))
                            self.wait(.2)
                            continue
                        self.navigator.invalidate()
                        points[index]['position'] = approach
                        points[index]['_approach_for'] = list(live_target['position'])
                if self.maintain_stamina(index, len(points)) or self.maintain_tool(index, len(points)):
                    anchor, navigation_leg, navigation_wait_since = None, None, None
                    best, progress_at = math.inf, self.clock.monotonic()
                    if self.navigator:
                        self.navigator.invalidate()
                    continue
                if self.direction_method and not special_near and self.collection is None:
                    memory=self.read_direction()
                    if memory is not None:
                        if basis is None or self.basis_angle(basis,memory)>policy.COORD_DIRECTION_STABLE_DEGREES:
                            self.release()
                            anchor,navigation_leg,navigation_wait_since=None,None,None
                            self.direction_evidence=DirectionEvidence()
                            if self.navigator:self.navigator.invalidate()
                        basis=memory
                        self.direction_source='memory'
                        if not selected:
                            reference=self.read(force=True)
                            index=choose_start_index(points,reference,self.start_mode)
                            self.start_index,selected=index,True
                        self.resume_from_current=False
                    elif not (self.direction_source=='probe' and basis is not None and self.direction_safe_probe):
                        current=self.read(force=True)
                        may_probe=self.direction_safe_probe and self.direction_bad_since is not None \
                            and self.clock.monotonic()-self.direction_bad_since>=policy.COORD_DIRECTION_WAIT_SECONDS
                        if not may_probe or not self.safe_direction_probe(current,index):
                            self.wait_direction(index,len(points))
                            anchor,navigation_leg,navigation_wait_since=None,None,None
                            continue
                        basis=None
                        reference=current
                        self.calibration_seconds=policy.COORD_DIRECTION_PROBE_SECONDS
                if basis is None and self.collection is None and not special_near:
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
                        if self.navigator:
                            scene = self.navigator.scene_provider() or {}
                            scope = repr((scene.get('cache_context'), scene.get('terrain_revision')))
                            if not self.calibration_budget.begin(reference, (self.cycle, index), scope):
                                raise _SkipPoint('同一区域方向核对次数已达上限，当前方向无法可靠确认')
                        basis = self.calibrate(reference, target_index=index)
                        if self.direction_method:
                            self.direction_source='probe'
                        if self.heading_cache is not None:
                            self.heading_cache['basis'] = basis
                        self.direction_evidence = DirectionEvidence()
                    except _CalibrationRetry as exc:
                        self.release()
                        reference, basis, anchor = self.position, None, None
                        if self.navigator:
                            self.navigator.invalidate()
                        self.report('calibrating', f'点位 {index + 1}/{len(points)} · {exc}，稍后重新校准 · F9停止',
                                    waypoint=index + 1, waypoint_count=len(points), navigation_path=[],
                                    incident_kind='calibration_failed', target=points[index]['position'])
                        scene_ready = (self.navigator.scene_provider() or {}).get('state') == 'ready'
                        if self.direction_method:
                            self.direction_bad_since=self.clock.monotonic()
                            self.wait_direction(index,len(points))
                        elif self.jump_on_recovery and scene_ready:
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
                    best, progress_at = math.inf, self.clock.monotonic()
                position = self.read()
                if self.navigator and self.waypoints is not None:
                    self.travel_progress.bind((self.cycle, index), position,
                                              points[index]['position'], self.calibration_speed)
                if segment_start is None:
                    segment_start = position
                held_keys = set(self.keys)
                target = points[index]['position']
                remaining = distance(position, target)
                if self.collection is not None and remaining > policy.COORD_REACH_RADIUS:
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
                if remaining <= policy.COORD_REACH_RADIUS and (
                        'special_resource' not in points[index] or special_near):
                    self.set_keys(set())
                    if self.navigator and self.collection is None:
                        # Allow inertia and landing to settle before pressing F.
                        arrival = position
                        self.wait(policy.COORD_SETTLE_SECONDS)
                        position = self.read(force=True)
                        if distance(position, target) > policy.COORD_REACH_RADIUS \
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
                        self.navigator.collection_outcome = ('action_completed_unconfirmed_loot'
                            if self.collect_wait_seconds is not None else 'not_requested')
                        self.navigator.arrived(position)
                    if self.waypoints is not None:
                        text = '已完成采集与等待' if self.collect_wait_seconds is not None else '已到达点位'
                        if 'special_resource' in points[index-1]:
                            evidence = (self.special_progress or {}).get('evidence')
                            text = ('已确认采到（背包增量核对）' if evidence and evidence.fields()['resource_confirmed']
                                    else SPECIAL_REACHED_TEXT[(self.special_progress or {}).get('outcome')])
                        if self.navigator and self.navigator.memory_state == 'write_error':
                            text += '（走法记忆暂未保存）'
                        self.report('reached', f'{text} {index}/{len(points)} · F6暂停 · F9停止',
                                    position=list(position), waypoint=index, waypoint_count=len(points))
                    self.collection = None
                    self.special_progress = None
                    dwell, anchor = None, None
                    self.recovery.reset()
                    if self.navigator:
                        self.navigator.invalidate()
                    navigation_leg = None
                    segment_start = position
                    best, progress_at = math.inf, now
                    if self.waypoints is not None and index == len(points):
                        partial = self.cycle in self.skipped_cycles
                        self.report('cycle_partial' if partial else 'cycle_finished',
                                    '本轮有未完成点位' if partial else '本轮点位回放完成 · F6暂停 · F9停止',
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
                self.travelling = True
                if self.navigator:
                    planning_at = self.clock.monotonic()
                    self.navigation_waiting = True
                    try:
                        self.navigator.learning_finished = None
                        self.navigator.learning_time = self.active_time
                        self.navigator.set_waypoint(index)
                        target = self.navigator.next_target(position, target, check=self.guard, before_plan=self.release)
                    except NavigationWait as exc:
                        self.release()
                        if exc.reason == 'no_path' and self.continuous_recovery and self.recovery_attempts:
                            navigation_wait_since = now if navigation_wait_since is None else navigation_wait_since
                            if now - navigation_wait_since >= policy.COORD_NAV_RECOVERY_WAIT_SECONDS:
                                self.navigation_waiting = False
                                self.last_tick = self.clock.monotonic()
                                self.approach_attempt(index)
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
                                        waypoint=index + 1, waypoint_count=len(points), navigation_path=[],
                                        navigation_wait_reason=exc.reason, target=points[index]['position'])
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
                physically_stalled = bool(self.navigator and self.waypoints is not None
                                           and self.travel_progress.stalled(position))
                if remaining < best - policy.COORD_STUCK_DISTANCE and not physically_stalled:
                    best, progress_at = remaining, now
                elif physically_stalled or now - progress_at >= policy.COORD_STUCK_SECONDS:
                    if self.waypoints is None:
                        raise ValueError('角色未向目标前进，可能遇到障碍，已停止')
                    attempted_keys = sorted(self.keys)
                    self.release()
                    try:
                        from utils.core.playback_trace import collision_context
                        context = collision_context(self.navigator, position, target)
                    except Exception as exc:
                        # Optional diagnostics must never abort recovery.
                        context = {'state': 'unavailable', 'contact_confirmed': False,
                                   'diagnostic_error': str(exc)}
                    self.report('recovering', f'点位 {index + 1}/{len(points)} · 检测到移动无进展，已记录现场',
                                waypoint=index+1, waypoint_count=len(points), position=list(position),
                                target=list(points[index]['position']), steering_target=list(target),
                                input_keys=attempted_keys, incident_kind='movement_stall',
                                stalled_seconds=max(now-progress_at,
                                    self.travel_progress.elapsed-self.travel_progress.motion_at
                                    if physically_stalled else 0.),
                                physical_stall=physically_stalled,
                                point_drive_seconds=self.travel_progress.elapsed,
                                collision_context=context)
                    if self.navigator:
                        self.approach_attempt(index)
                        self.navigator.blocked(position, target,
                                               attempt_id=(self.cycle,index,self.approach_attempts))
                    try:
                        improved = False
                        # A small advance followed by another stall (or an
                        # interrupted action) remains the same failed episode.
                        self.fail_recovery(index, len(points))
                        while self.recovery.failures < self.recovery_attempts:
                            if self.recover(target, basis, segment_start, index, len(points)):
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
                elif self.navigator and anchor is not None and anchor_keys == held_keys \
                        and held_keys and now - anchor_at >= policy.COORD_CALIBRATION_SECONDS:
                    displacement = [b - a for a, b in zip(anchor, position)]
                    length = math.hypot(displacement[0], displacement[2])
                    vector = key_vector(held_keys, basis)
                    norm = math.hypot(*vector)
                    projected = (anchor[0] + vector[0] / norm * length, anchor[1],
                                 anchor[2] + vector[1] / norm * length)
                    # Both corridors must be clear: wall-sliding is not evidence
                    # of camera rotation. Large verified changes recalibrate here.
                    if length >= policy.COORD_MIN_CALIBRATION:
                        observation = self.direction_evidence.observe(
                            vector, (displacement[0], displacement[2]), now=now,
                            geometry_clear=self.navigator.motion_clear(anchor, position)
                            and self.navigator.motion_clear(anchor, projected),
                            height_delta=displacement[1], mode='jump' if 'space' in held_keys else
                            'transient' if now < self.direction_unsafe_until else 'walk',
                            scope=repr((self.navigator.scene or {}).get('cache_context')))
                        if observation['state'] == 'aligned':
                            self.navigator.motion_succeeded(anchor,position)
                            if self.direction_method and now>anchor_at:
                                measured=math.hypot(displacement[0],displacement[2])/(now-anchor_at)
                                if 0.<measured<=policy.COORD_MAX_SPEED:
                                    self.calibration_speed=.8*self.calibration_speed+.2*measured
                        if self.direction_method and observation['state']=='rotation_candidate':
                            self.direction_conflict=self.direction_memory or basis
                            self.direction_memory=self.direction_candidate=None
                            self.direction_safe_probe=True
                            self.direction_bad_since=self.clock.monotonic()
                            self.release()
                            reference,basis,navigation_leg,anchor=position,None,None,None
                            self.navigator.invalidate()
                            self.report('navigating','正常行走与方向读数持续不符，先重新核对；未盲目校准',
                                direction_source='motion_conflict',direction_observation=observation)
                            continue
                        if not self.direction_method and (observation['state'] == 'rotation_candidate' or (
                                observation['state'] == 'unknown' and abs(observation.get('angle_degrees', 0))
                                > policy.COORD_MAX_DIRECTION_CHANGE_DEGREES)):
                            self.release()
                            self.navigator.invalidate()
                            reference, basis, navigation_leg, anchor = position, None, None, None
                            self.report('calibrating', '方向存在偏差，有限次数核对；尚未确认镜头变化',
                                        waypoint=index + 1, waypoint_count=len(points), navigation_path=[],
                                        incident_kind='direction_uncertain', direction_observation=observation)
                            continue
                    anchor, anchor_at = position, now
                special_steering = ({'reach_radius': policy.COORD_SPECIAL_APPROACH_PRECISION}
                                    if 'special_resource' in points[index] else {})
                desired = direction_keys(position, target, basis, **special_steering)
                if self.heading_cache is not None:
                    self.heading_cache['basis'] = basis
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
                    navigation = ({'navigation_path': [list(p) for p in self.navigator.path],
                                   'navigation_decision': self.navigator.last_decision,
                                   'approach_attempts': self.approach_attempts} if self.navigator else {})
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
                self.release()
                self.travelling = False
                self.skipped_count += 1
                self.skipped_cycles.add(self.cycle)
                self.report('skipped', f'点位 {index+1}/{len(points)} 暂时跳过：{exc}',
                            waypoint=index+1, skipped_count=self.skipped_count, incident_kind='point_skipped',
                            skip_reason=str(exc), point_drive_seconds=self.travel_progress.elapsed,
                            point_drive_budget=self.travel_progress.budget)
                index += 1
                self.collection, self.special_progress = None, None
                self.recovery.reset()
                reference, basis, anchor = self.position, None, None
                dwell, segment_start, navigation_leg, navigation_wait_since = None, self.position, None, None
                best, progress_at = math.inf, self.clock.monotonic()
                self.navigator.invalidate()
                if index == len(points):
                    self.report('cycle_partial', '本轮有未完成点位', skipped_count=self.skipped_count)
                    if self.cycle < self.loop_count:
                        self.cycle += 1
                        index, self.start_index = 0, 0
                        self.active_time, self.last_tick = 0., self.clock.monotonic()
            except _Paused:
                self.release()
                self.direction_poll_at=float('-inf')
                self.direction_candidate=self.direction_memory=self.direction_conflict=None
                self.direction_bad_since=None
                self.direction_safe_probe=False
                self.direction_source='legacy'
                if self.heading_cache is not None:
                    self.heading_cache.clear()
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
        if self.skipped_count:
            self.report('partial', f'回放结束，{self.skipped_count} 个点位流程未完成，详见回放记录',
                        skipped_count=self.skipped_count)
            return False
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
                 repair_box=None, tool_selector=None, food=None, scene_refresh=None, food_fallback='fruit',
                 heading_cache=None, harvest_journal=None):
    if 'special_patrol' in data:
        from utils.flows.special_patrol import drive_special_patrol
        return drive_special_patrol(data, size, reader_factory, device, window, controls, report,
            clock=clock, navigator=navigator, point_runner=drive_points, scene_refresh=scene_refresh,
            start_mode=start_mode, recovery_attempts=recovery_attempts, jump_on_recovery=jump_on_recovery,
            collect_wait_seconds=collect_wait_seconds, loop_count=loop_count,
            continuous_recovery=continuous_recovery, repair_box=repair_box, tool_selector=tool_selector,
            food=food, food_fallback=food_fallback)
    reader = None
    try:
        if food_fallback not in FALLBACK_MODES:
            raise ValueError('无料理时的食物范围无效')
        points = ordered_waypoints(data)
        validate_loop_count(loop_count)
        validate_recovery_options(recovery_attempts, jump_on_recovery)
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
        config = {'duration': 0., 'client_size': list(size)}
        follower = _Follower(config, reader, device, window, controls, report, clock,
                         waypoints=points, start_mode=start_mode, recovery_attempts=recovery_attempts,
                         jump_on_recovery=jump_on_recovery, collect_wait_seconds=collect_wait_seconds,
                         loop_count=loop_count, continuous_recovery=continuous_recovery, navigator=navigator,
                         repair_box=repair_box, tool_selector=tool_selector, food=food,
                         scene_refresh=scene_refresh)
        follower.food_fallback = food_fallback
        follower.heading_cache = heading_cache
        follower.harvest_journal = harvest_journal
        return follower.follow()
    except _Stopped:
        return False
    finally:
        try:
            device.release_all()
        finally:
            if reader:
                reader.close()


def play_main(base, data):
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
