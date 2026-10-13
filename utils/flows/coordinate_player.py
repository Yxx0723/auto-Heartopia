"""Position feedback playback, with bounded calibration and injectable devices."""
import math
from pathlib import Path

from utils.core import thresholds as policy
from utils.core.coordinate_points import (choose_start_index, ordered_waypoints, validate_collect_wait,
                                          validate_loop_count)
from utils.core.coordinate_recovery import (RecoveryProgress, recovery_plan, recovery_within_limits,
                                             validate_recovery_options)
from utils.core.coordinate_route import (build_waypoints, direction_keys, distance,
                                         make_basis, rotate_basis, valid_position,
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


class _RecoveryLimit(Exception):
    pass


class _Follower:
    def __init__(self, data, reader, device, window, controls, report, clock, waypoints=None, start_mode='first',
                 recovery_attempts=0, jump_on_recovery=False, collect_wait_seconds=None, loop_count=1):
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
        self.resume_from_current = False
        self.collect_wait_seconds, self.collection = collect_wait_seconds, None
        self.last_collect_report = float('-inf')

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
        if self.last_tick is not None:
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
        if not recovery_within_limits(position, self.recovery.origin, segment_start, target):
            raise _RecoveryLimit()
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
        if not recovery_within_limits(target, self.recovery.origin, segment_start, route_target):
            raise _RecoveryLimit()
        end = self.clock.monotonic() + policy.COORD_RECOVERY_LEG_SECONDS
        try:
            while self.clock.monotonic() < end:
                position = self.recovery_position(segment_start, route_target)
                if distance(position, target) <= policy.COORD_REACH_RADIUS:
                    return True
                keys = direction_keys(position, target, basis)
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
        if not recovery_within_limits(position, self.recovery.origin, segment_start, target):
            raise _RecoveryLimit()
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
        while index < len(points):
            try:
                self.guard()
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
                    basis = self.calibrate(reference, target_index=index)
                    if segment_start is None:
                        segment_start = self.position
                    anchor = None
                    best, progress_at = math.inf, self.clock.monotonic()
                position = self.read()
                target = points[index]['position']
                remaining = distance(position, target)
                if self.collection is not None and remaining > policy.COORD_REACH_RADIUS:
                    raise ValueError('采集等待期间离开了当前点位，已停止，请重新开始回放')
                if abs(position[1] - target[1]) > policy.COORD_MAX_HEIGHT_ERROR \
                        and (self.waypoints is None or remaining <= policy.COORD_REACH_RADIUS):
                    raise ValueError('当前位置与路线高度不一致，请回到相同场景和楼层')
                now = self.clock.monotonic()
                if self.waypoints is not None and self.recovery.observe(position, target):
                    self.report('playing', f'点位 {index + 1}/{len(points)} · 已恢复前进，连续失败已清零'
                                ' · F6暂停 · F9停止', waypoint=index + 1, waypoint_count=len(points),
                                recovery_result='cleared', recovery_failures=0,
                                recovery_limit=self.recovery_attempts)
                if remaining <= policy.COORD_REACH_RADIUS:
                    self.set_keys(set())
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
                    if self.waypoints is not None:
                        text = '已完成采集与等待' if self.collect_wait_seconds is not None else '已到达点位'
                        self.report('reached', f'{text} {index}/{len(points)} · F6暂停 · F9停止',
                                    position=list(position), waypoint=index, waypoint_count=len(points))
                    self.collection = None
                    dwell, anchor = None, None
                    self.recovery.reset()
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
                if remaining < best - policy.COORD_STUCK_DISTANCE:
                    best, progress_at = remaining, now
                elif now - progress_at >= policy.COORD_STUCK_SECONDS:
                    if self.waypoints is None:
                        raise ValueError('角色未向目标前进，可能遇到障碍，已停止')
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
                    except _RecoveryLimit:
                        self.pause_blocked(index, len(points), '脱困移动达到距离或高度限制')
                    if not improved:
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
                desired = direction_keys(position, target, basis)
                if anchor is None or desired != anchor_keys:
                    anchor, anchor_keys, anchor_at = position, set(desired), now
                self.set_keys(desired)
                if now - self.last_report >= .5:
                    self.report('playing', f'坐标回放 {index + 1}/{len(points)} · F6暂停 · F9停止',
                                position=list(position), waypoint=index + 1, waypoint_count=len(points))
                    self.last_report = now
                self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
            except _Paused:
                self.release()
                if self.position is not None:
                    reference = self.position
                self.last_tick, self.position_at, basis = None, None, None
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
                 loop_count=policy.COORD_PLAYBACK_DEFAULT_LOOPS):
    reader = None
    try:
        points = ordered_waypoints(data)
        validate_loop_count(loop_count)
        validate_recovery_options(recovery_attempts, jump_on_recovery)
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
                         loop_count=loop_count).follow()
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
