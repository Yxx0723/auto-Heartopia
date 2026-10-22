"""Jump and detour execution using the shared session input guard."""
from utils.core import thresholds as policy
from utils.core.coordinate_recovery import recovery_plan, recovery_limit_reason
from utils.core.coordinate_route import direction_keys, distance
from utils.flows.playback_signals import _Paused, _RecoveryLimit, _SkipPoint


class RecoveryActions:
    def recovery_position(self, segment_start, target, force=False):
        self.direction_quiet_until = self.clock.monotonic() + policy.COORD_DIRECTION_RECOVERY_QUIET_SECONDS
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
                    keys = self.navigator.detour_keys(position, target, basis,
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
        if distance(before, target) <= policy.COORD_REACH_RADIUS:
            # A navigator leg or a resource ring can already be within the
            # ordinary point radius while the real resource is still outside
            # its interaction envelope. It supplies no useful detour bearing.
            point = self.waypoints[index] if self.waypoints is not None else None
            candidate = point['position'] if point is not None else target
            if point is not None and 'special_resource' in point:
                candidate = self.special_target(point, allow_global=True)['position']
            if distance(before, candidate) <= policy.COORD_REACH_RADIUS:
                if self.navigator:
                    self.navigator.invalidate()
                return False  # Recheck arrival/replan; never guess a direction.
            target = candidate
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
            if self.navigator:
                self.navigator.invalidate()
                self.report('navigating', f'点位 {index+1}/{total} · 大跳未通过，重新寻路 · F9停止',
                            waypoint=index+1, waypoint_count=total, navigation_path=[])
                return False
        self.report('recovering', f'点位 {index + 1}/{total} · 左右试走校正 '
                    f'· 连续失败 {self.recovery.failures}/{self.recovery_attempts} · F6暂停 · F9停止',
                    position=list(self.position), waypoint=index + 1, waypoint_count=total,
                    recovery_phase='detour', recovery_attempt=self.recovery.attempts,
                    recovery_limit=self.recovery_attempts, recovery_failures=self.recovery.failures)
        # Preserve alternating sides around the same obstruction. If an old
        # episode origin coincides with a newly selected target, use current
        # feet for this probe instead of attempting to normalise a zero vector.
        plan_origin = self.recovery.origin if distance(self.recovery.origin, target) > policy.COORD_REACH_RADIUS else before
        plan = recovery_plan(plan_origin, target, self.recovery.attempts)
        detoured = True
        for point in plan:
            if not self.recovery_leg(point, basis, segment_start, target):
                detoured = False
                break
        if self.stable_navigation:
            # Only a genuine stalled traversal reaches here. Prefer a checked
            # lateral detour before spending a jump; computation budget is not
            # a physical obstacle and never enters recovery.
            improved = detoured and distance(self.read(force=True), target) < distance(before, target) - policy.COORD_STUCK_DISTANCE
            return improved
        if not detoured:
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
        if self.skip_unreachable and self.collection is None:
            raise _SkipPoint(reason)
        if self.data.get('patrol_probe'):
            from utils.core.special_patrol import PatrolLegBlocked
            raise PatrolLegBlocked(reason)
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
        if self.skip_unreachable and self.recovery.failures >= self.recovery_attempts:
            raise _SkipPoint('绕行和脱困连续失败，转入稍后补采')
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
