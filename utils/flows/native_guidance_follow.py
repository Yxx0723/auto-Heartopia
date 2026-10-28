"""Follow a task-local cached game route after F6; no interactions."""
import math

from utils.core import thresholds as policy
from utils.core.guidance_path import (GuidanceCursor, GuidanceRouteCache, GuidanceRecovery,
                                      validate_guidance_state)
from utils.core.coordinate_navigation import NavigationWait
from utils.core.guidance_navigation import GuidanceNavigator
from utils.drivers.windows_input import PreciseClock
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
        self.accepted = ((self.cache.route['context'], self.cache.route['target'])
                         if self.cache.route else None)
        self.cursor = None
        self.in_recovery = False
        self.episode_arc = None
        self.waiting = GuidanceRecovery()
        self.read_stats = {'full_reads': 0, 'status_reads': 0}

    def guard(self):
        super().guard()
        if self.in_recovery and self.clock.monotonic() >= self.refresh_at:
            try:
                self.refresh_guidance(self.position, force=True)
            except (OSError, ValueError, TypeError) as exc:
                self.cache.healthy_at = None
                raise _GuidanceRetry(str(exc)) from exc

    def same_target(self, snapshot):
        return self.accepted is not None and snapshot['context'] == self.accepted[0] \
            and math.dist(snapshot['target'], self.accepted[1]) <= policy.COORD_REACH_RADIUS

    def pause_guidance(self, message):
        self.release()
        pause = getattr(self.controls, 'pause_with_reason', None)
        if callable(pause):
            pause(message+' · F6确认后继续 · F9停止')
        else:
            self.controls.pause()
        self.report('paused', message+' · F6确认后继续 · F9停止', navigation_path=[])
        raise _Paused()

    def refresh_guidance(self, position, force=False):
        now = self.clock.monotonic()
        if not force and now < self.refresh_at:
            return self.cache.require_healthy(now)
        self.release()  # No input held during memory reads or route replacement.
        started = now
        full = self.cache.route is None or not self.cache.token or not callable(self.status_source) \
            or now-self.cache.loaded_at >= policy.COORD_GUIDANCE_AUDIT_SECONDS
        raw = self.source() if full else self.status_source()
        self.read_stats['full_reads' if full else 'status_reads'] += 1
        while True:
            if isinstance(raw, dict) and raw.get('state') == 'inactive':
                raise _GuidanceEnded()
            validate_guidance_state(raw, self.clock.monotonic())
            if self.accepted is not None and not self.same_target(raw):
                self.pause_guidance('追踪目标或场景已改变，请按 F6 重新确认')
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
            cursor = GuidanceCursor(snapshot, position)
            self.cursor = cursor
            self.navigator.invalidate()
            self.report('guidance_path', '完整游戏引导路线已缓存 · F6暂停 · F9停止',
                        navigation_path=list(cursor.path), target=list(cursor.target),
                        guidance_revision=cursor.revision, navigation_mode='native_guidance')
        if self.accepted is None:
            self.accepted = (snapshot['context'], snapshot['target'])
        self.read_stats['read_seconds'] = max(0.,self.clock.monotonic()-started)
        self.read_stats['route_points'] = len(snapshot['path'])
        self.refresh_at = self.clock.monotonic()+policy.COORD_GUIDANCE_REFRESH_SECONDS
        return snapshot

    def wait_for_input_data(self, stage, exc):
        self.release()
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
                        self.release()
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
                            target = self.navigator.next_target(position, aim['position'], check=self.guard,
                                                                before_plan=self.release)
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
                                                                        before_plan=self.release)
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
                    self.release()
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
                self.set_keys(self.navigator.steering_keys(position, target, basis, travel))
                if self.clock.monotonic()-self.last_report >= .5:
                    self.report('playing', f'跟随缓存引导 · 路线剩余约 {aim["remaining"]:.1f} 单位 · F6暂停 · F9停止',
                                navigation_path=list(self.cursor.path), target=list(self.cursor.target),
                                guidance_revision=self.cursor.revision, navigation_mode='native_guidance',
                                guidance_metrics=dict(self.read_stats))
                    self.last_report = self.clock.monotonic()
                self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
            except _GuidanceRetry:
                # The normal guidance stage owns the retry deadline and can
                # safely enter the outer pause handler on its next iteration.
                self.cache.healthy_at = None
                self.refresh_at = 0.
                self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
            except _GuidanceEnded:
                self.release()
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
    reader = None
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
        return GuidanceFollower(size, reader, device, window, controls, report, clock, navigator, prepared).follow_guidance()
    except _Stopped:
        return {'arrived': False, 'reason': '已停止，按键已释放'}
    finally:
        try:
            device.release_all()
        finally:
            if reader:
                reader.close()
