"""Follow a task-local cached game route after F6; no interactions."""
import math

from utils.core import thresholds as policy
from utils.core.guidance_path import (GuidanceCursor, GuidanceRouteCache, GuidanceRecovery,
                                      validate_guidance_state, guidance_identity, guidance_identity_change)
from utils.core.coordinate_navigation import NavigationWait
from utils.core.guidance_navigation import GuidanceNavigator
from utils.drivers.windows_input import PreciseClock
from utils.drivers.guidance_input_lease import GuidanceInputLease
from utils.flows.coordinate_player import (_Follower, _Paused, _Stopped, _SkipPoint, _RecoveryLimit)


class _GuidanceEnded(Exception):
    pass


class _GuidanceRetry(Exception):
    pass


class GuidanceFollower(_Follower):
    def __init__(self, size, reader, device, window, controls, report, clock, navigator, prepared=None):
        super().__init__({'duration': 0., 'client_size': list(size)}, reader, device, window, controls,
                         report, clock, waypoints=[{'position': (0, 0, 0)}],
                         navigator=navigator, recovery_attempts=policy.COORD_RECOVERY_DEFAULT_ATTEMPTS,
                         jump_on_recovery=True, continuous_recovery=True)
        self.source = getattr(reader, 'guidance', None)
        if not callable(self.source):
            raise ValueError('当前读取器不支持游戏原生引导')
        self.status_source = getattr(reader, 'guidance_status', None)
        self.cache = prepared or GuidanceRouteCache()
        self.refresh_at = 0.
        self.accepted = guidance_identity(self.cache.route) if self.cache.route else None
        self.cursor = None
        self.in_recovery = False
        self.episode_arc = None
        self.waiting = GuidanceRecovery()
        self.read_stats = {'full_reads': 0, 'status_reads': 0}
        self.input_lease = GuidanceInputLease(device, controls, window, list(size), clock.monotonic) \
            if isinstance(navigator, GuidanceNavigator) else None
        self.move_deadline = float('-inf')
        self.anchor_rejoin_reported = None

    def read(self, force=False):
        started = self.clock.monotonic()
        try:
            return super().read(force)
        finally:
            self.read_stats['position_read_seconds'] = max(0., self.clock.monotonic()-started)

    def read_direction(self):
        started = self.clock.monotonic()
        try:
            return super().read_direction()
        finally:
            self.read_stats['direction_read_seconds'] = max(0., self.clock.monotonic()-started)

    def report(self, state, message, **extra):
        if self.input_lease is not None:
            extra.setdefault('input_continuity', self.input_lease.metrics())
            extra.setdefault('navigation_timings', dict(self.navigator.last_timings))
        return super().report(state, message, **extra)

    def release(self, reason='controller'):
        if self.input_lease is None:
            return super().release()
        self.input_lease.release(reason, active=bool(self.keys))
        self.keys.clear()

    def planning_release(self):
        self.release(getattr(self.navigator, 'release_reason', 'planning_or_map_change'))

    def set_keys(self, keys):
        if self.input_lease is None:
            return super().set_keys(keys)
        if self.in_recovery:
            result = super().set_keys(keys)
            self.input_lease.observe_keys(self.keys)
            return result
        with self.input_lease.lock:
            self.guard()
            if self.clock.monotonic() >= self.move_deadline:
                self.release('permit_not_ready')
                raise _GuidanceRetry('移动许可到期，重新核对')
            try:
                self.input_lease.arm(self.move_deadline)
            except ValueError as exc:
                self.release('permit_not_ready')
                raise _GuidanceRetry(str(exc)) from exc
            try:
                result = super().set_keys(keys)
                self.input_lease.observe_keys(self.keys)
                return result
            except BaseException:
                self.release('input_exception')
                raise

    def guard(self):
        if self.input_lease is not None:
            self.input_lease.poll()
            with self.input_lease.lock:
                if self.input_lease.error:
                    raise OSError('独立按键释放失败') from self.input_lease.error
                if self.input_lease.tripped:
                    self.keys.clear()
                    self.input_lease.tripped = False
                    self.cache.healthy_at = None
                    raise _GuidanceRetry('移动许可已撤销，重新核对')
        super().guard()
        if self.in_recovery and self.clock.monotonic() >= self.refresh_at:
            try:
                self.refresh_guidance(self.position, force=True)
            except (OSError, ValueError, TypeError) as exc:
                self.cache.healthy_at = None
                raise _GuidanceRetry(str(exc)) from exc

    def same_target(self, snapshot):
        return self.accepted is not None and guidance_identity_change(self.accepted,guidance_identity(snapshot)) is None

    def pause_guidance(self, message, **evidence):
        self.release('guidance_pause')
        pause = getattr(self.controls, 'pause_with_reason', None)
        if callable(pause):
            pause(message+' · F6确认后继续 · F9停止')
        else:
            self.controls.pause()
        self.report('paused', message+' · F6确认后继续 · F9停止', navigation_path=[], **evidence)
        raise _Paused()

    def refresh_guidance(self, position, force=False):
        now = self.clock.monotonic()
        if not force and now < self.refresh_at:
            return self.cache.require_healthy(now)
        if self.input_lease is None:
            self.release()  # Keep legacy/injected planner behavior.
        # A read never renews the existing short movement permit. The independent
        # watchdog releases keys if a synchronous read blocks beyond that permit.
        started = now
        full = self.cache.route is None or not self.cache.token or not callable(self.status_source) \
            or now-self.cache.loaded_at >= policy.COORD_GUIDANCE_AUDIT_SECONDS
        raw = self.source() if full else self.status_source()
        self.read_stats['full_reads' if full else 'status_reads'] += 1
        while True:
            if isinstance(raw, dict) and raw.get('state') == 'inactive':
                raise _GuidanceEnded()
            if isinstance(raw, dict) and raw.get('state') == 'pending':
                raise ValueError('追踪仍存在，引导路线正在更新，稍后自动重试')
            validate_guidance_state(raw, self.clock.monotonic())
            current_identity = guidance_identity(raw)
            reason = guidance_identity_change(self.accepted,current_identity) if self.accepted is not None else None
            if reason:
                messages = {'scene_context_changed':'游戏引导场景身份已改变，请按 F6 重新确认',
                            'tracking_id_changed':'游戏追踪目标已改变，请按 F6 重新确认',
                            'tracking_identity_unavailable':'追踪目标身份未确认，请按 F6 重新确认',
                            'legacy_target_coordinates_changed':'目标坐标已改变，当前来源缺少追踪 ID，请按 F6 重新确认'}
                old_target = self.cache.route['target'] if self.cache.route else self.accepted[1]
                self.pause_guidance(messages[reason],guidance_pause_reason=reason,
                    previous_context=self.cache.route['context'] if self.cache.route else self.accepted[0],
                    observed_context=raw['context'],previous_scene_context=self.accepted[0],
                    observed_scene_context=current_identity[0],
                    previous_tracking_id=self.accepted[2],tracking_id=current_identity[2],
                    previous_target=list(old_target),observed_target=list(raw['target']),
                    target_delta_xz=math.hypot(raw['target'][0]-old_target[0],raw['target'][2]-old_target[2]),
                    target_delta_y=raw['target'][1]-old_target[1])
            if full:
                self.cache.install(raw, self.clock.monotonic())
                break
            if self.cache.confirm(raw, self.clock.monotonic()):
                break
            raw, full = self.source(), True
            self.read_stats['full_reads'] += 1
        snapshot = self.cache.require_healthy(self.clock.monotonic())
        if self.cursor is None or self.cursor.revision != snapshot['revision']:
            # Construct fully before publishing the new cursor. Reconnect near
            # the current position, never resume a historical start index.
            self.release('guidance_route_changed')
            cursor = GuidanceCursor(snapshot, position)
            self.cursor = cursor
            self.navigator.invalidate()
            self.report('guidance_path', '完整游戏引导路线已缓存 · F6暂停 · F9停止',
                        navigation_path=list(cursor.path), target=list(cursor.target),
                        guidance_revision=cursor.revision, navigation_mode='native_guidance',
                        tracking_id=snapshot.get('tracking_id'))
        if self.accepted is None:
            self.accepted = guidance_identity(snapshot)
        self.read_stats['read_seconds'] = max(0.,self.clock.monotonic()-started)
        self.read_stats['route_points'] = len(snapshot['path'])
        self.refresh_at = self.clock.monotonic()+policy.COORD_GUIDANCE_REFRESH_SECONDS
        return snapshot

    def wait_for_input_data(self, stage, exc):
        self.release('wait_'+stage)
        if stage == 'guidance':
            self.cache.healthy_at = None
            self.refresh_at = 0.
        now = self.clock.monotonic()
        if self.waiting.failed(stage, now):
            self.pause_guidance(f'{stage} 持续未恢复：{exc}')
        if now-self.last_report >= .5:
            self.report('navigating', f'{exc} · 正在自动重试 · 按键已释放 · F9停止',
                        navigation_path=[], wait_stage=stage,
                        navigation_wait_reason=getattr(exc,'reason',stage),
                        wait_seconds=now-self.waiting.since[stage], guidance_metrics=dict(self.read_stats))
            self.last_report = now
        self.clock.sleep(policy.COORD_SAMPLE_SECONDS)

    def follow_guidance(self):
        while True:
            try:
                self.guard()
                self.travelling = False
                position = self.read()
                stage = 'guidance'
                try:
                    snapshot = self.refresh_guidance(position)
                    self.waiting.passed('guidance')
                    self.waypoints[0]['position'] = snapshot['target']
                    self.travel_progress.bind(self.accepted, position, snapshot['target'], self.calibration_speed)
                    aim = self.cursor.aim(position)
                    stage = 'direction'
                    basis = self.read_direction() if self.direction_method else None
                    if basis is None:
                        raise ValueError('等待可靠方向数据，未执行走动校准')
                    self.waiting.passed('direction')
                    self.direction_source = 'memory'
                    if aim['arrived']:
                        self.release('arrived')
                        return {'arrived': True, 'target_distance': aim['target_distance'],
                                'reason': '已到游戏引导线末端附近，未执行交互'}
                    stage = 'scene'
                    if not isinstance(self.navigator, GuidanceNavigator):
                        self.release()
                    planned_revision = self.cursor.revision
                    started = self.clock.monotonic()
                    self.navigation_waiting = True
                    try:
                        try:
                            if isinstance(self.navigator, GuidanceNavigator):
                                target = self.navigator.follow_target(self.cursor, position, aim, self.guard,
                                                                      self.planning_release)
                            else:
                                target = self.navigator.next_target(position, aim['position'], check=self.guard,
                                                                    before_plan=self.planning_release)
                        except NavigationWait as original:
                            if original.reason != 'no_path' or not isinstance(self.navigator, GuidanceNavigator):
                                raise
                            # A very close display point may lie in collision
                            # padding. Try bounded forward positions on the same
                            # route, through the SAME checked planner, never a
                            # blind straight-line fallback.
                            for factor in (2,4):
                                later = self.cursor.aim(position,lookahead=policy.COORD_GUIDANCE_LOOKAHEAD*factor)
                                if abs(later['height_hint']-position[1])>policy.COORD_GUIDANCE_HEIGHT_HINT_BAND:
                                    continue
                                try:
                                    target = self.navigator.next_target(position,later['position'],check=self.guard,
                                                                        before_plan=self.planning_release)
                                except NavigationWait as retry:
                                    if retry.reason != 'no_path':raise
                                    continue
                                self.report('guidance_rejoin','局部引导点暂不可达，已规划接入前方路线',
                                            guidance_rejoin=True, target=list(later['position']))
                                break
                            else:
                                raise original
                    finally:
                        self.navigation_waiting = False
                        self.last_tick = self.clock.monotonic()
                    if isinstance(self.navigator, GuidanceNavigator) and self.navigator.anchor_rejoin:
                        token = (self.cursor.revision, self.navigator.anchor.point)
                        if token != self.anchor_rejoin_reported:
                            self.anchor_rejoin_reported = token
                            self.report('guidance_rejoin', '已通过碰撞检查，接入前方引导线', guidance_rejoin=True)
                    self.waiting.passed('scene')
                    self.read_stats['plan_seconds'] = max(0.,self.clock.monotonic()-started)
                    stage = 'guidance'
                    # A slow plan requires fresh health, not destruction of the
                    # unchanged route and another slow planning-only iteration.
                    self.refresh_guidance(position)
                    self.waiting.passed('guidance')
                    if self.cursor.revision != planned_revision:
                        continue
                    if self.read_stats['plan_seconds'] >= policy.COORD_GUIDANCE_REFRESH_SECONDS:
                        current = self.read(force=True)
                        if math.dist(current,position) > policy.COORD_STUCK_DISTANCE:
                            continue
                        stage = 'direction'
                        basis = self.read_direction()
                        if basis is None:
                            raise ValueError('等待规划后的可靠方向数据')
                        self.waiting.passed('direction')
                    self.guard()
                    stage = 'guidance'
                    self.cache.require_healthy(self.clock.monotonic())
                    self.waiting.ready()
                except (OSError, ValueError, TypeError, NavigationWait) as exc:
                    self.release('invalid_'+stage)
                    if isinstance(exc,NavigationWait) and exc.reason != 'scene':
                        stage = 'path'
                        if self.clock.monotonic()-self.last_report>=.5:
                            self.report('navigation_issue',str(exc),navigation_wait_reason=exc.reason,
                                        incident_kind='navigation_wait',local_target=list(aim['position']),
                                        navigation_evidence=self.navigator.explain(position,aim['position'])
                                            if isinstance(self.navigator,GuidanceNavigator) else None,
                                        navigator_stats=dict(self.navigator.stats))
                    self.wait_for_input_data(stage, exc)
                    continue
                self.travelling = True
                if self.episode_arc is not None and self.cursor.arc-self.episode_arc >= policy.COORD_RECOVERY_CONFIRM_DISTANCE:
                    self.recovery.reset()
                    self.episode_arc = None
                if self.travel_progress.stalled(position):
                    self.release()
                    if self.recovery.attempts >= self.recovery_attempts:
                        return {'arrived': False, 'reason': '当前引导段连续脱困未通过，已停止并释放按键'}
                    if self.episode_arc is None:
                        self.episode_arc = self.cursor.arc
                    self.navigator.blocked(position, target, attempt_id=(self.accepted, self.recovery.attempts))
                    self.report('recovering', '游戏引导段移动无进展，尝试局部脱困',
                                incident_kind='movement_stall', position=list(position), target=list(target))
                    self.in_recovery = True
                    attempts_before = self.recovery.attempts
                    try:
                        if not self.recover(target, basis, position, 0, 1):
                            self.fail_recovery(0, 1)
                    except _RecoveryLimit:
                        self.fail_recovery(0, 1)
                    except _GuidanceRetry:
                        self.recovery.attempts = attempts_before
                        raise
                    finally:
                        self.in_recovery = False
                        self.release()
                    self.navigator.invalidate()
                    continue
                travel = max(.15, self.calibration_speed*policy.COORD_SAMPLE_SECONDS*2)
                if self.input_lease is not None:
                    now = self.clock.monotonic()
                    expiry = self.navigator.collision_deadline(now)
                    if expiry is None:
                        raise _GuidanceRetry('碰撞许可已失效')
                    self.move_deadline = min(
                        self.position_at+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS,
                        self.cache.healthy_at+policy.COORD_GUIDANCE_MAX_AGE, expiry,
                        self.position_at+math.dist(position, target)/max(3., self.calibration_speed)*.5)
                self.set_keys(self.navigator.steering_keys(position, target, basis, travel))
                if self.clock.monotonic()-self.last_report >= .5:
                    self.report('playing', f'跟随缓存引导 · 路线剩余约 {aim["remaining"]:.1f} 单位 · F6暂停 · F9停止',
                                navigation_path=list(self.cursor.path), target=list(self.cursor.target),
                                guidance_revision=self.cursor.revision, navigation_mode='native_guidance',
                                guidance_metrics=dict(self.read_stats),tracking_id=snapshot.get('tracking_id'),
                                input_continuity=self.input_lease.metrics() if self.input_lease else None)
                    self.last_report = self.clock.monotonic()
                self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
            except _GuidanceRetry:
                self.release('guidance_retry')
                # The normal guidance stage owns the retry deadline and can
                # safely enter the outer pause handler on its next iteration.
                self.cache.healthy_at = None
                self.refresh_at = 0.
                self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
            except _GuidanceEnded:
                self.release('guidance_ended')
                return {'arrived': False, 'reason': '游戏追踪已结束，已停止跟随'}
            except _Paused:
                self.release()
                self.travelling = False
                self.accepted = self.cursor = None
                self.cache = GuidanceRouteCache()
                self.refresh_at = 0.
                self.direction_candidate = self.direction_memory = None
                self.direction_poll_at = float('-inf')
                self.position_at = self.last_tick = None
                self.navigator.invalidate()
                self.waiting.ready()
                self.clock.sleep(.01)
            except _SkipPoint as exc:
                self.release()
                return {'arrived': False, 'reason': str(exc)}


def drive_guidance(size, reader_factory, device, window, controls, report, clock=PreciseClock, *, navigator):
    reader = follower = None
    try:
        if not isinstance(size, (list, tuple)) or len(size) != 2 \
                or any(type(v) is not int or not 64 <= v <= 16384 for v in size):
            raise ValueError('游戏窗口尺寸无效')
        if controls.snapshot()[1]:
            return {'arrived': False, 'reason': '已停止，按键已释放'}
        device.release_all()
        reader = reader_factory()
        prepared = GuidanceRouteCache()
        started = clock.monotonic()
        last_report = float('-inf')
        while prepared.route is None:
            if controls.snapshot()[1]:
                return {'arrived': False, 'reason': '已停止，按键已释放'}
            try:
                prepared.install(reader.guidance(),clock.monotonic())
            except (OSError,ValueError,TypeError) as exc:
                if clock.monotonic()-started >= policy.COORD_GUIDANCE_RETRY_SECONDS:
                    return {'arrived':False,'reason':f'完整引导路线暂无法缓存：{exc}'}
                if clock.monotonic()-last_report>=.5:
                    report('playback_wait',f'正在缓存完整引导路线：{exc} · F9停止')
                    last_report=clock.monotonic()
                clock.sleep(policy.COORD_SAMPLE_SECONDS)
        report('playback_wait',f'已缓存 {len(prepared.route["path"])} 个引导点 · 切回游戏按 F6 开始 · F9停止',
               navigation_path=list(prepared.route['path']),guidance_revision=prepared.route['revision'])
        while True:
            running, stopped = controls.snapshot()
            if stopped:
                return {'arrived': False, 'reason': '已停止，按键已释放'}
            if running:
                break
            clock.sleep(.01)
        follower = GuidanceFollower(size, reader, device, window, controls, report, clock, navigator, prepared)
        if follower.input_lease is not None:
            follower.input_lease.start()
        return follower.follow_guidance()
    except _Stopped:
        return {'arrived': False, 'reason': '已停止，按键已释放'}
    finally:
        try:
            if follower is not None and follower.input_lease is not None:
                follower.input_lease.close()
        finally:
            try:
                device.release_all()
            finally:
                if reader:
                    reader.close()
