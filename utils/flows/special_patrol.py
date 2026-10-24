"""Bounded house tour, live discovery and confirmed collection on existing navigation."""
from copy import deepcopy
from datetime import datetime
import math

from utils.core import thresholds as policy
from utils.core.coordinate_route import distance
from utils.core.coordinate_navigation import ObstacleMap
from utils.core.special_patrol import validate_patrol, patrol_target, SpecialTargetUnavailable
from utils.core import special_verdict


class _Found(Exception):
    pass


class _Arrived(Exception):
    pass


class _Expired(Exception):
    pass


class _Stopped(Exception):
    pass


class _Lost(Exception):
    pass


class _Reader:
    def __init__(self, reader, watch):
        self.reader, self.watch = reader, watch

    def read(self):
        position = self.reader.read()
        self.watch(position)
        return position

    def __getattr__(self, name):
        return getattr(self.reader, name)


class _Controls:
    def __init__(self, controls, tick):
        self.controls, self.tick = controls, tick

    def snapshot(self):
        running, stopped = self.controls.snapshot()
        self.tick(running, stopped)
        return running, stopped

    def __getattr__(self, name):
        return getattr(self.controls, name)


def drive_special_patrol(data, size, reader_factory, device, window, controls, report,
                         clock=None, navigator=None, point_runner=None, scene_refresh=None,
                         date_clock=None, **options):
    from utils.drivers.windows_input import PreciseClock
    from utils.flows.coordinate_player import drive_points
    clock, point_runner = clock or PreciseClock, point_runner or drive_points
    validate_patrol(data)
    if navigator is None:
        raise ValueError('房门巡查需要实时场景导航')
    cfg = data['special_patrol']
    name, category = data['points'][0]['name'], cfg['category']
    houses = list(zip(cfg['checkpoint_houses'], cfg['checkpoints']))
    checked, failed, attempted = set(), {}, set()
    rejected = []
    target_attempts = 0
    position, last_scene, last_refresh = None, None, -math.inf
    unknown_since = None
    # Context identity is independent of streamed collision geometry revisions.
    context = deepcopy(cfg['context'])
    date_clock = date_clock or (lambda: datetime.now().astimezone().date())
    start_date = date_clock()

    def emit(message, state='patrolling', **extra):
        report(state, f'{name} · {message} · F6暂停 · F9停止',
               patrol_checked=len(checked), patrol_total=12,
               patrol_missing=sorted(set(range(1, 13))-checked), **extra)

    def scene_now():
        nonlocal last_scene, last_refresh
        now = clock.monotonic()
        if date_clock() != start_date:
            raise ValueError('日期已切换，请重新核对当天资源并启动巡查')
        if now-last_refresh >= policy.COORD_PATROL_POLL_SECONDS or last_scene is None:
            if scene_refresh:
                scene_refresh()  # Wake the existing worker; never read game memory on this thread.
            last_scene, last_refresh = navigator.scene_provider() or {}, now
        s = last_scene
        if s.get('state') == 'ready' and s.get('patrol_context') is not None \
                and s['patrol_context'] != context:
            raise ValueError('游戏场景或小镇已切换，请重新启动巡查')
        return s

    def current_target(s):
        if s.get('patrol_context') != context:
            return 'wait', None
        state, obj = patrol_target(s, category)
        if state == 'live' and (target_attempts >= policy.COORD_PATROL_MAX_TARGET_ATTEMPTS
                               or any(math.dist(obj['position'], p) < .5 for p in rejected)):
            return 'search', None
        return state, obj

    def wait_active(seconds, discover=False):
        elapsed, previous, was_running = 0., clock.monotonic(), False
        while elapsed < seconds:
            running, stopped = controls.snapshot()
            now = clock.monotonic()
            if stopped:
                raise _Stopped()
            if running and was_running:
                elapsed += now-previous
            previous = now
            if running and (not window.foreground() or window.size() != size):
                controls.pause(); running = False
            was_running = running
            if running:
                s = scene_now()
                if discover and current_target(s)[0] == 'live':
                    return True
            else:
                device.release_all()
            clock.sleep(.01)
        return False

    def run_leg(goal, number=None, collect=False, survey_center=None):
        point = deepcopy(data['points'][0])
        point['position'] = list(goal)
        if not collect:
            point.pop('special_resource')
            point['name'], point['patrol_house'] = f'{number}号房门前', number
        leg = {'schema': 'coordinate-points-v1', 'points': [point], 'patrol_probe': True}
        elapsed, last, was_running = 0., clock.monotonic(), False
        outcome = None
        maintaining = False
        press_started = False
        limit = policy.COORD_PATROL_TARGET_SECONDS if collect else min(
            policy.COORD_PATROL_MAX_LEG_SECONDS, max(policy.COORD_PATROL_MIN_LEG_SECONDS,
                distance(position, goal)/1.5+20.))

        def tick(running, stopped):
            nonlocal elapsed, last, was_running
            now = clock.monotonic()
            if stopped:
                raise _Stopped()
            if running and was_running and not maintaining:
                elapsed += now-last
            last, was_running = now, running
            if not running or not window.foreground() or window.size() != size:
                return
            if elapsed > limit:
                raise _Expired()
            scene_now()

        def watch(p):
            nonlocal position
            position = tuple(p)
            running, stopped = controls.snapshot()
            tick(running, stopped)
            if maintaining:
                return  # Coordinate polling during menus is not resumed navigation.
            if running and not collect and current_target(scene_now())[0] == 'live':
                raise _Found()
            if running and collect and outcome is None and not press_started:
                state, obj = current_target(scene_now())
                if state != 'live' or math.dist(obj['position'], goal) > 3.:
                    raise _Lost()
            center = survey_center if survey_center is not None else goal
            if running and not collect and distance(p, center) <= policy.COORD_PATROL_HOUSE_RADIUS \
                    and abs(p[1]-center[1]) <= policy.COORD_MAX_HEIGHT_ERROR:
                raise _Arrived()

        def leg_report(state, message, **extra):
            nonlocal outcome, maintaining, press_started
            outcome = extra.get('resource_outcome', outcome)
            if state == 'collecting' and 'resource_press_count' in extra:
                press_started = True  # Let the existing before/after sampler finish its verdict.
            if state in ('eating', 'repairing', 'equipping', 'food_paused', 'repair_paused', 'equip_paused'):
                maintaining = True
            elif state in ('playing', 'navigating', 'collecting', 'calibrating', 'recovering'):
                maintaining = False
            if state in ('cycle_finished', 'cycle_partial', 'partial'):
                state = 'patrol_progress'  # An ephemeral leg is not a completed user route.
            emit(message, state=state, **{**extra, 'patrol_phase': 'collect' if collect else 'search',
                                        'patrol_house': number})

        navigator.invalidate()
        try:
            done = point_runner(leg, size, lambda: _Reader(reader_factory(), watch), device,
                        window, _Controls(controls, tick), leg_report, clock=clock,
                        **{**options, 'start_mode': 'first', 'loop_count': 1,
                           'collect_wait_seconds': 0. if collect else None,
                           'navigator': navigator, 'scene_refresh': scene_refresh})
            return done, outcome
        except _Arrived:
            return True, None
        finally:
            device.release_all()

    try:
        # Do not open the position reader before the user's F6 start.
        while True:
            running, stopped = controls.snapshot()
            if stopped:
                return False
            if running and window.foreground() and window.size() == size:
                break
            if running:
                controls.pause()
            clock.sleep(.01)
        reader = reader_factory()
        try:
            position = tuple(reader.read())
        finally:
            reader.close()
        emit('开始按 1→12 号房门巡查，门前不是固定刷新点', patrol_phase='start')
        pending = houses[:]
        retry_round = False
        while True:
            running, stopped = controls.snapshot()
            if stopped:
                return False
            if not running or not window.foreground() or window.size() != size:
                if running:
                    controls.pause()
                device.release_all(); clock.sleep(.01)
                unknown_since = None
                continue
            s = scene_now()
            state, obj = current_target(s)
            if state == 'wait':
                unknown_since = clock.monotonic() if unknown_since is None else unknown_since
                if clock.monotonic()-unknown_since > policy.COORD_PATROL_DATA_WAIT_SECONDS:
                    emit('场景或资源身份持续未确认，请重新连接后重试', state='partial',
                         patrol_result='data_unavailable')
                    return False
                emit('等待场景与资源身份同步，不按 F', patrol_phase='wait')
                device.release_all(); wait_active(.5)
                continue
            unknown_since = None
            if state == 'live':
                target_attempts += 1
                emit('发现实时资源，前往交互范围再次确认', patrol_phase='collect',
                     patrol_goal=list(obj['position']), patrol_source='live-scene')
                try:
                    done, outcome = run_leg(obj['position'], collect=True)
                    if done and outcome == special_verdict.GATHERED:
                        emit('已确认采到，任务结束', state='patrol_finished', patrol_result='gathered',
                             resource_outcome=outcome, resource_confirmed=True)
                        return True
                    running, stopped = controls.snapshot()
                    if stopped:
                        return False
                    rejected.append(list(obj['position']))
                    emit('本次采集结果未确认，避免重复按键，继续房门巡查',
                         patrol_result='unconfirmed', resource_outcome=outcome)
                except (_Expired, _Lost, SpecialTargetUnavailable):
                    rejected.append(list(obj['position']))
                    emit('目标接近超时或实体失效，继续巡查', patrol_result='unconfirmed')
                continue
            if not pending:
                if failed and not retry_round:
                    pending = [(n, p) for n, p in houses if n in failed]
                    retry_round = True
                    emit('本轮结束，补查未到达的房门一次', patrol_phase='retry')
                    continue
                result = 'unconfirmed' if rejected else 'partial' if failed else 'not_found'
                emit('本轮巡查结束；未确认资源不代表全图没有', state='patrol_finished' if not failed else 'partial',
                     patrol_result=result, patrol_failed=sorted(failed), patrol_attempted=sorted(attempted),
                     resource_confirmed=False)
                return not failed and not rejected
            number, goal = pending[0]
            emit(f'前往 {number}/12 号房门前', patrol_phase='search', patrol_house=number,
                 patrol_goal=list(goal), patrol_retry=retry_round)
            attempted.add(number)
            try:
                obstacles = ObstacleMap(s, goal, goal=goal, water_areas=navigator.water_areas)
                candidates = [list(goal)] + [[goal[0]+radius*math.cos(i*math.tau/16), goal[1],
                                             goal[2]+radius*math.sin(i*math.tau/16)]
                                            for radius in (1., 2.) for i in range(16)]
                candidates = [p for p in candidates if obstacles.clear((p[0], p[2]), (p[0], p[2]))]
                if not candidates:
                    raise _Expired()
                approach = min(candidates, key=lambda p: distance(position, p))
                done, _ = run_leg(approach, number=number, survey_center=goal)
                if not done:
                    running, stopped = controls.snapshot()
                    if stopped:
                        return False
                    raise _Expired()
                emit(f'已到 {number} 号房门前，等待附近实体加载', patrol_phase='scan', patrol_house=number)
                if wait_active(policy.COORD_PATROL_DWELL_SECONDS, discover=True):
                    continue  # Resume this house's observation after collection if necessary.
                checked.add(number); failed.pop(number, None); pending.pop(0)
            except _Found:
                continue  # Keep interrupted checkpoint pending.
            except _Expired:
                pending.pop(0); failed[number] = 'navigation_timeout_or_incomplete'
                emit(f'{number}号房暂时无法到达，继续其他房门', patrol_result='unreachable',
                     patrol_house=number, patrol_reason=failed[number])
    except _Stopped:
        return False
    finally:
        device.release_all()
