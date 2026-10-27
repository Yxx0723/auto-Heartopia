"""Follow current game guidance after F6. No collection or menu interactions."""
import math

from utils.core import thresholds as policy
from utils.core.guidance_path import GuidanceCursor, validate_guidance
from utils.core.coordinate_navigation import NavigationWait
from utils.drivers.windows_input import PreciseClock
from utils.flows.coordinate_player import (_Follower, _Paused, _Stopped, _SkipPoint, _RecoveryLimit)


class GuidanceFollower(_Follower):
    def __init__(self, size, reader, device, window, controls, report, clock, navigator):
        super().__init__({'duration': 0., 'client_size': list(size)}, reader, device, window, controls,
                         report, clock, waypoints=[{'position': (0, 0, 0)}],
                         navigator=navigator, recovery_attempts=policy.COORD_RECOVERY_DEFAULT_ATTEMPTS,
                         jump_on_recovery=True, continuous_recovery=True)
        self.source = getattr(reader, 'guidance', None)
        if not callable(self.source):
            raise ValueError('当前读取器不支持游戏原生引导')
        self.raw_guidance = None
        self.refresh_at = 0.
        self.accepted = None
        self.cursor = None
        self.in_recovery = False
        self.episode_arc = None

    def guard(self):
        super().guard()
        if self.in_recovery and self.clock.monotonic() >= self.refresh_at:
            self.release()
            try:
                self.raw_guidance = self.source()
                current = validate_guidance(self.raw_guidance, self.clock.monotonic())
            except (OSError, ValueError, TypeError) as exc:
                self.pause_guidance(f'脱困期间引导失效：{exc}')
            if not self.same_target(current):
                self.pause_guidance('追踪目标或场景已改变，请按 F6 重新确认')
            self.refresh_at = self.clock.monotonic()+policy.COORD_GUIDANCE_REFRESH_SECONDS

    def same_target(self, snapshot):
        return self.accepted is not None and snapshot['context'] == self.accepted[0] \
            and math.dist(snapshot['target'], self.accepted[1]) <= policy.COORD_REACH_RADIUS

    def pause_guidance(self, message):
        self.release()
        self.controls.pause()
        self.report('paused', message+' · F6确认后继续 · F9停止', navigation_path=[])
        raise _Paused()

    def follow_guidance(self):
        issue_since = None
        while True:
            try:
                self.guard()
                self.travelling = False
                position = self.read()
                try:
                    if self.raw_guidance is None or self.clock.monotonic() >= self.refresh_at:
                        self.release()  # Never hold input across process/list reads.
                        self.raw_guidance = self.source()
                        self.refresh_at = self.clock.monotonic()+policy.COORD_GUIDANCE_REFRESH_SECONDS
                    if self.raw_guidance.get('state') == 'inactive':
                        return {'arrived': False, 'reason': '游戏追踪已结束，已停止跟随'}
                    snapshot = validate_guidance(self.raw_guidance, self.clock.monotonic())
                    if self.accepted is not None and not self.same_target(snapshot):
                        self.pause_guidance('追踪目标或场景已改变，请按 F6 重新确认')
                    if self.cursor is None or snapshot['revision'] != self.cursor.revision:
                        self.release()
                        self.cursor = GuidanceCursor(snapshot, position)
                        self.navigator.invalidate()
                        self.report('guidance_path', '已读取当前游戏引导路径 · F6暂停 · F9停止',
                                    navigation_path=list(self.cursor.path), target=list(self.cursor.target),
                                    guidance_revision=self.cursor.revision, navigation_mode='native_guidance')
                    if self.accepted is None:
                        self.accepted = (snapshot['context'], snapshot['target'])
                    self.waypoints[0]['position'] = snapshot['target']
                    self.travel_progress.bind(self.accepted, position, snapshot['target'], self.calibration_speed)
                    aim = self.cursor.aim(position)
                    basis = self.read_direction() if self.direction_method else None
                    if basis is None:
                        raise ValueError('等待可靠方向数据，未执行走动校准')
                    self.direction_source = 'memory'
                    if aim['arrived']:
                        self.release()
                        return {'arrived': True, 'target_distance': aim['target_distance'],
                                'reason': '已到游戏引导线末端附近，未执行交互'}
                    self.release()
                    self.navigation_waiting = True
                    try:
                        target = self.navigator.next_target(position, aim['position'], check=self.guard,
                                                            before_plan=self.release)
                    finally:
                        self.navigation_waiting = False
                        self.last_tick = self.clock.monotonic()
                    if self.clock.monotonic() >= self.refresh_at:
                        self.release()
                        self.raw_guidance = None
                        continue  # Revalidate the source after slow planning.
                    validate_guidance(self.raw_guidance, self.clock.monotonic())
                    issue_since = None
                except (OSError, ValueError, TypeError, NavigationWait) as exc:
                    self.release()
                    now = self.clock.monotonic()
                    issue_since = now if issue_since is None else issue_since
                    if now-issue_since >= policy.COORD_GUIDANCE_WAIT_SECONDS:
                        self.pause_guidance(str(exc))
                    if now-self.last_report >= .5:
                        self.report('navigating', f'{exc} · 按键已释放 · F9停止', navigation_path=[])
                        self.last_report = now
                    self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
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
                    try:
                        if not self.recover(target, basis, position, 0, 1):
                            self.fail_recovery(0, 1)
                    except _RecoveryLimit:
                        self.fail_recovery(0, 1)
                    finally:
                        self.in_recovery = False
                        self.release()
                    self.navigator.invalidate()
                    continue
                travel = max(.15, self.calibration_speed*policy.COORD_SAMPLE_SECONDS*2)
                self.set_keys(self.navigator.steering_keys(position, target, basis, travel))
                if self.clock.monotonic()-self.last_report >= .5:
                    self.report('playing', f'跟随游戏引导 · 路线剩余约 {aim["remaining"]:.1f} 单位 · F6暂停 · F9停止',
                                navigation_path=list(self.cursor.path), target=list(self.cursor.target),
                                guidance_revision=self.cursor.revision, navigation_mode='native_guidance')
                    self.last_report = self.clock.monotonic()
                self.clock.sleep(policy.COORD_SAMPLE_SECONDS)
            except _Paused:
                self.release()
                self.travelling = False
                self.accepted = self.cursor = self.raw_guidance = None
                self.direction_candidate = self.direction_memory = None
                self.direction_poll_at = float('-inf')
                self.position_at = self.last_tick = None
                self.navigator.invalidate()
                issue_since = None
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
        while True:
            running, stopped = controls.snapshot()
            if stopped:
                return {'arrived': False, 'reason': '已停止，按键已释放'}
            if running:
                break
            clock.sleep(.01)
        reader = reader_factory()
        return GuidanceFollower(size, reader, device, window, controls, report, clock, navigator).follow_guidance()
    except _Stopped:
        return {'arrived': False, 'reason': '已停止，按键已释放'}
    finally:
        try:
            device.release_all()
        finally:
            if reader:
                reader.close()
