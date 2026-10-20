"""Position feedback playback, with bounded calibration and injectable devices."""
import math
from pathlib import Path

from utils.core import thresholds as policy
from utils.core.coordinate_points import (choose_start_index, ordered_waypoints, validate_collect_wait,
                                          validate_loop_count)
from utils.core.coordinate_navigation import NavigationWait
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


class _RecoveryLimit(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _Follower:
    def __init__(self, data, reader, device, window, controls, report, clock, waypoints=None, start_mode='first',
                 recovery_attempts=0, jump_on_recovery=False, collect_wait_seconds=None, loop_count=1,
                 continuous_recovery=False, navigator=None, repair_box=None, tool_selector=None, food=None):
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
                self.budget += sum(p.get('collect_wait', collect_wait_seconds)
                                   + policy.COORD_COLLECTION_PRESS_SECONDS for p in waypoints)
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
        self.meal_pending = self.meal_menu_pending = False
        self.meal_before = self.meal_deadline = 0.
        self.meal_settle_until = 0.
        self.food_menu_checked = False
        self.repair_menu_pending = False

    def report(self, state, message, **extra):
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
            self.active_time += max(0., now - self.last_tick)
        self.last_tick = now
        if self.active_time > self.budget:
            raise ValueError('回放超过时限，已停止，请缩短路线后重试')

    def read(self, force=False):
        self.guard()
        started = self.clock.monotonic()
        if not force and self.position_at is not None and started - self.position_at < policy.COORD_SAMPLE_SECONDS:
            return self.position
        position = self.reader.read()
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
        if self.jump_on_recovery:
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
        if self.collection is None:
            self.collection = {'sent': False, 'remaining': point.get('collect_wait', self.collect_wait_seconds)}
        self.release()
        self.collection_position(point)
        if not self.collection['sent']:
            if self.food is not None:
                self.read_stamina()
                if self.maintain_stamina(index, total):
                    return False
            if self.tool_selector is not None:
                self.read_tool()
                if self.maintain_tool(index, total):
                    return False
            self.report('collecting', f'点位 {index + 1}/{total} · 按 F 采集 · F6暂停 · F9停止',
                        waypoint=index + 1, waypoint_count=total)
            try:
                self.set_keys({'f'})
                self.collection['sent'] = True
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
        return self.collection['remaining'] <= 0

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
            self.repair_pause(f'关闭背包未完成：{exc}', index, total, state='food_paused')
        finally:
            self.release()
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()

    def maintain_stamina(self, index, total):
        if self.food is None:
            return False
        if self.clock.monotonic()-self.last_stamina_check >= 1.:
            self.read_stamina()
        status = self.stamina_status
        if not usable_stamina(status):
            self.release()
            for _ in range(3):
                self.wait(.1)
                status = self.read_stamina()
                if usable_stamina(status):
                    break
        if not usable_stamina(status):
            self.repair_pause('暂时无法读取本地角色体力，请等待读取恢复', index, total, state='food_paused')
        if status['current'] >= 30 and not self.meal_pending and not self.meal_menu_pending:
            # Starting or resuming may leave a backpack from manual eating or
            # an earlier run open. Check once before movement, never per tick.
            if not self.food_menu_checked:
                return self.close_stamina_menu(index, total)
            return False
        self.release()
        self.navigation_waiting = True
        try:
            self.report('eating', f'体力 {status["current"]} / {status["maximum"]}，正在补充水果 · F6暂停 · F9停止',
                        waypoint=index+1, waypoint_count=total)
            if self.clock.monotonic() < self.meal_settle_until:
                self.wait(self.meal_settle_until-self.clock.monotonic())
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('体力正在更新，请稍后继续')
            if not self.meal_pending and status['current'] < 30:
                self.meal_menu_pending = True
                target = self.food.prepare(self.window, self.repair_tap, self.wait, self.guard, self.device)
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('食用前无法确认体力')
                if status['current'] < 30:
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
                self.meal_pending = False
                raise ValueError('食用后体力未增加，请检查水果数量或食用动作')
            if self.meal_pending:
                self.meal_pending = False
                self.meal_settle_until = self.clock.monotonic()+2.
                self.wait(2.)  # Keep the confirmed eating animation out of movement/calibration.
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('体力正在更新，请稍后继续')
            if status['current'] >= 30:
                if self.meal_menu_pending:
                    self.food.close_menu(self.window, self.repair_tap, self.guard, self.wait, self.device)
                    self.meal_menu_pending = False
                    self.food_menu_checked = True
                self.report('playing', f'体力已恢复至 {status["current"]}，继续当前点位 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total)
            return True
        except (ValueError, OSError) as exc:
            self.repair_pause(f'自动吃水果未完成：{exc}', index, total, state='food_paused')
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
                if self.maintain_stamina(index, len(points)) or self.maintain_tool(index, len(points)):
                    anchor, navigation_leg, navigation_wait_since = None, None, None
                    best, progress_at = math.inf, self.clock.monotonic()
                    if self.navigator:
                        self.navigator.invalidate()
                    continue
                if basis is None and self.collection is None:
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
                        if self.jump_on_recovery and scene_ready:
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
                if remaining <= policy.COORD_REACH_RADIUS:
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
                        if self.navigator and self.navigator.memory_state == 'write_error':
                            text += '（走法记忆暂未保存）'
                        self.report('reached', f'{text} {index}/{len(points)} · F6暂停 · F9停止',
                                    position=list(position), waypoint=index, waypoint_count=len(points))
                    self.collection = None
                    dwell, anchor = None, None
                    self.recovery.reset()
                    if self.navigator:
                        self.navigator.invalidate()
                    navigation_leg = None
                    segment_start = position
                    best, progress_at = math.inf, now
                    if self.waypoints is not None and index == len(points):
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
                        self.navigator.set_waypoint(index)
                        target = self.navigator.next_target(position, target, check=self.guard, before_plan=self.release)
                    except NavigationWait as exc:
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
                elif now - progress_at >= policy.COORD_STUCK_SECONDS:
                    if self.waypoints is None:
                        raise ValueError('角色未向目标前进，可能遇到障碍，已停止')
                    if self.navigator:
                        self.navigator.blocked(position, target)
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
                    if length >= policy.COORD_MIN_CALIBRATION and self.navigator.motion_clear(anchor, position) \
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
                desired = direction_keys(position, target, basis)
                if self.navigator:
                    desired = self.navigator.steering_keys(position, target, basis,
                        max(.15, self.calibration_speed * policy.COORD_SAMPLE_SECONDS * 2))
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
            except _Paused:
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
                 repair_box=None, tool_selector=None, food=None):
    reader = None
    try:
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
        return _Follower(config, reader, device, window, controls, report, clock,
                         waypoints=points, start_mode=start_mode, recovery_attempts=recovery_attempts,
                         jump_on_recovery=jump_on_recovery, collect_wait_seconds=collect_wait_seconds,
                         loop_count=loop_count, continuous_recovery=continuous_recovery, navigator=navigator,
                         repair_box=repair_box, tool_selector=tool_selector, food=food).follow()
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
