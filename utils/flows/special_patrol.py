"""Search loaded regions, verify arrival, then use the existing gather runner."""
from copy import deepcopy
import math

from utils.core import thresholds as policy
from utils.core.coordinate_points import validate_points
from utils.core.coordinate_route import distance
from utils.core.coordinate_navigation import ObstacleMap
from utils.core.special_approach import in_interaction_range, SpecialTargetUnavailable
from utils.core.special_patrol import patrol_target, PatrolLegBlocked


class _Found(Exception):
    pass


class _Stopped(Exception):
    pass


class _LegExpired(PatrolLegBlocked):
    pass


class _Controls:
    """Count active time, including no-path waits, but excluding F6 pauses."""
    def __init__(self, controls, clock):
        self.controls, self.clock = controls, clock
        self.last, self.was_running, self.elapsed = None, False, 0.

    def snapshot(self):
        running, stopped = self.controls.snapshot()
        now = self.clock.monotonic()
        if self.last is not None and self.was_running and running:
            self.elapsed += max(0., now - self.last)
        self.last, self.was_running = now, running
        return running, stopped

    def __getattr__(self, name):
        return getattr(self.controls, name)


class _Reader:
    def __init__(self, reader, watch):
        self.reader, self.watch = reader, watch

    def read(self):
        return self.read_for_playback()

    def read_for_playback(self):
        position = getattr(self.reader, 'read_for_playback', self.reader.read)()
        self.watch(position)
        return position

    def __getattr__(self, name):
        return getattr(self.reader, name)


def drive_special_patrol(data, size, reader_factory, device, window, controls, report,
                         clock=None, navigator=None, point_runner=None, scene_refresh=None, **options):
    if point_runner is None:
        from utils.flows.coordinate_player import drive_points
        point_runner = drive_points
    if clock is None:
        from utils.drivers.clock import PreciseClock
        clock = PreciseClock
    validate_points(data)
    if navigator is None:
        raise ValueError('动态巡查需要实时场景导航')
    config = data['special_patrol']
    category, name = config['category'], data['points'][0]['name']
    pending = [list(p) for p in config['checkpoints']]
    total = len(pending)
    rejected, blocked_live, checked = [], [], 0
    position, world_context = None, None

    def scene_now():
        nonlocal world_context
        scene = navigator.scene_provider() or {}
        context = scene.get('global_special_context')
        if scene.get('state') == 'ready' and context is not None:
            if world_context is not None and context != world_context:
                raise ValueError('游戏世界已切换，请重新启动本次巡查')
            world_context = deepcopy(context)
        return scene

    def emit(message, **extra):
        report('patrolling', f'{name} · {message} · F6暂停 · F9停止',
               patrol_checked=checked, patrol_total=total, navigation_path=[], **extra)

    def target_now(scene):
        state, target = patrol_target(scene, category, rejected)
        if state == 'live' and any(math.dist(target['position'], p) <= 2. for p in blocked_live):
            return 'search', None
        return state, target

    def check_controls(active):
        running, stopped = active.snapshot()
        if stopped:
            raise _Stopped()
        if running and (not window.foreground() or window.size() != size):
            active.pause()
            return False
        return running

    def idle(seconds, look_for_target=True):
        active = _Controls(controls, clock)
        refresh_at = float('-inf')
        while active.elapsed < seconds:
            if check_controls(active):
                if scene_refresh is not None and clock.monotonic() - refresh_at >= .25:
                    scene_refresh()
                    refresh_at = clock.monotonic()
                state, _ = target_now(scene_now())
                if look_for_target and state in ('live', 'unavailable'):
                    return True
            clock.sleep(.01)
        return False

    def run_leg(goal, collect=False):
        active = _Controls(controls, clock)
        limit = (policy.COORD_PATROL_TARGET_SECONDS if collect else
                 min(policy.COORD_PATROL_MAX_LEG_SECONDS,
                     max(policy.COORD_PATROL_MIN_LEG_SECONDS, distance(position, goal) / 1.5 + 20.)))
        refresh_at = float('-inf')
        def watch(current):
            nonlocal position, refresh_at
            position = tuple(current)
            if not check_controls(active):
                return
            if active.elapsed > limit:
                raise _LegExpired('本次巡查落脚点等待或通行超时')
            if scene_refresh is not None and clock.monotonic() - refresh_at >= .25:
                scene_refresh()
                refresh_at = clock.monotonic()
            state, _ = target_now(scene_now())
            if not collect and state in ('live', 'unavailable'):
                raise _Found()
        point = deepcopy(data['points'][0])
        point['position'] = list(goal)
        if not collect:
            point.pop('special_resource', None)
            point['name'] = '巡查区域'
        leg = {'schema': 'coordinate-points-v1', 'points': [point], 'patrol_probe': True}
        navigator.invalidate()
        kwargs = {**options, 'start_mode': 'first', 'loop_count': 1,
                  'collect_wait_seconds': 0. if collect else None,
                  'navigator': navigator, 'scene_refresh': scene_refresh}
        def leg_report(state, message, **extra):
            report(state, (f'{name} · 巡查 {checked}/{total} · ' if not collect else '') + message,
                   **{**extra, 'patrol_phase': 'collect' if collect else 'search',
                      'patrol_checked': checked, 'patrol_total': total})
        return point_runner(leg, size, lambda: _Reader(reader_factory(), watch), device,
                            window, active, leg_report, clock=clock, **kwargs)

    def standpoint(seed, scene):
        # A fixed tree centre is a search area, not a standable destination.
        obstacles = ObstacleMap(scene, seed, water_areas=navigator.water_areas)
        angle = math.atan2(position[2] - seed[2], position[0] - seed[0])
        candidates = [[seed[0] + radius * math.cos(angle + turn * math.tau / 16), seed[1],
                       seed[2] + radius * math.sin(angle + turn * math.tau / 16)]
                      for radius in (2.5, 4.) for turn in range(16)]
        clear = [p for p in candidates if obstacles.clear((p[0], p[2]), (p[0], p[2]))]
        return min(clear, key=lambda p: distance(position, p)) if clear else None

    try:
        while True:
            running, stopped = controls.snapshot()
            if stopped:
                return False
            if running:
                break
            clock.sleep(.01)
        reader = reader_factory()
        try:
            position = tuple(getattr(reader, 'read_for_playback', reader.read)())
        finally:
            reader.close()
        emit('开始巡查，位置记录仅用于前往核查', patrol_phase='start')
        while True:
            active = _Controls(controls, clock)
            if not check_controls(active):
                clock.sleep(.01)
                continue
            scene = scene_now()
            state, target = target_now(scene)
            if state == 'wait':
                emit('等待场景同步或资源身份核对，不按 F', patrol_phase='wait')
                idle(.5, look_for_target=False)
                continue
            if state == 'unavailable':
                report('patrol_finished', f'{name}已加载，但当前采集标志不可用；不再按 F，请核对冷却状态',
                       resource_outcome='unavailable', navigation_path=[])
                return True
            if state == 'live':
                emit('已确认真实资源，接近后采集', patrol_phase='confirmed')
                try:
                    return run_leg(target['position'], collect=True)
                except (SpecialTargetUnavailable, PatrolLegBlocked) as exc:
                    rejected.append(list(target['position']))
                    blocked_live.append(list(target['position']))
                    emit(f'本次资源接近未完成：{exc}；继续巡查其他区域', patrol_result='unconfirmed')
                    # A still-visible but unreachable live target must not be
                    # selected again immediately. Scan elsewhere before retry.
                    state, target = 'search', None
            if state == 'hint':
                emit('前往位置线索，到点后确认实体；线索不代表可采集', patrol_phase='hint')
                try:
                    if not in_interaction_range(position, target['position']):
                        if not run_leg(target['position']):
                            return False
                    emit('已到位置线索，等待实体加载，不按 F', patrol_phase='confirm')
                    if idle(policy.COORD_SPECIAL_LOAD_WAIT_SECONDS):
                        continue
                    rejected.append(list(target['position']))
                    emit('到点未确认资源，停用这条线索并继续巡查', patrol_result='unconfirmed')
                except _Found:
                    continue
                except PatrolLegBlocked:
                    rejected.append(list(target['position']))
                    emit('位置线索暂时不可达，继续巡查其他区域', patrol_result='unreachable')
                continue
            if not pending:
                report('patrol_finished', f'{name}本轮可用巡查区域已结束，尚未确认目标；不代表全图没有',
                       resource_outcome='not_found', patrol_checked=checked, patrol_total=total, navigation_path=[])
                return True
            index = min(range(len(pending)), key=lambda i: distance(position, pending[i]))
            seed = pending.pop(index)
            checked += 1
            goal = standpoint(seed, scene)
            if goal is None:
                emit('区域暂时没有可站立位置，跳过后继续巡查', patrol_result='unreachable')
                continue
            emit(f'前往第 {checked}/{total} 个巡查区域', patrol_phase='search')
            try:
                if not run_leg(goal):
                    return False
                emit('已到巡查区域，刷新并核对附近实体，不按 F', patrol_phase='scan')
                idle(policy.COORD_PATROL_DWELL_SECONDS)
            except _Found:
                continue
            except PatrolLegBlocked:
                emit('区域暂时不可达，跳过后继续巡查', patrol_result='unreachable')
    except _Stopped:
        return False
    finally:
        device.release_all()
