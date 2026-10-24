"""Bounded house tour, live discovery and confirmed collection on existing navigation."""
from copy import deepcopy
from datetime import datetime
import math

from utils.core import thresholds as policy
from utils.core.coordinate_route import distance
from utils.core.coordinate_navigation import ObstacleMap
from utils.core.special_patrol import validate_patrol, patrol_target, patrol_checkpoints, SpecialTargetUnavailable
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
    combined=category=='both'
    required=cfg.get('categories',[category])
    completed, first_seen = set(), {}
    heading_cache = {}
    harvest_journal, inventory_reader, last_inventory_poll = [], None, -math.inf
    original_provider=navigator.scene_provider
    houses = list(zip(cfg['checkpoint_houses'], cfg['checkpoints']))
    checked, failed, attempted = set(), {}, set()
    rejected = []
    target_attempts = {k:0 for k in required}
    position, last_scene, last_refresh = None, None, -math.inf
    unknown_since = None
    # Context identity is independent of streamed collision geometry revisions.
    context = deepcopy(cfg['context'])
    date_clock = date_clock or (lambda: datetime.now().astimezone().date())
    start_date = date_clock()

    def emit(message, state='patrolling', **extra):
        report(state, f'{name} · {message} · F6暂停 · F9停止',
               patrol_checked=len(checked), patrol_total=12,
               patrol_completed_resources=sorted(completed),
               patrol_missing=sorted(set(range(1, 13))-checked), **extra)

    def scene_now():
        nonlocal last_scene, last_refresh
        now = clock.monotonic()
        if date_clock() != start_date:
            raise ValueError('日期已切换，请重新核对当天资源并启动巡查')
        if now-last_refresh >= policy.COORD_PATROL_POLL_SECONDS or last_scene is None:
            if scene_refresh:
                scene_refresh()  # Wake the existing worker; never read game memory on this thread.
            last_scene, last_refresh = original_provider() or {}, now
        s = last_scene
        if s.get('state') == 'ready' and s.get('patrol_context') is not None \
                and s['patrol_context'] != context:
            raise ValueError('游戏场景或小镇已切换，请重新启动巡查')
        reconcile_inventory(now)
        return s

    def reconcile_inventory(now):
        nonlocal last_inventory_poll
        if inventory_reader is None or now-last_inventory_poll < policy.COORD_SPECIAL_INVENTORY_POLL_SECONDS:
            return
        last_inventory_poll = now
        pending = [(at,e) for at,e in harvest_journal if e.pressed and not e.invalid
                   and e.category not in completed and now-at <= policy.COORD_SPECIAL_PENDING_SECONDS]
        if not pending:
            return
        method = getattr(inventory_reader, 'backpack_counts_snapshot', None)
        if not callable(method):
            return
        try:
            inventory = method()
        except (OSError, ValueError):
            return
        for _, evidence in pending:
            evidence.observe('unknown', inventory)
            if evidence.fields()['resource_confirmed'] and evidence.category not in completed:
                completed.add(evidence.category)
                emit('延迟背包增量已确认采到：'+('萤石' if evidence.category=='fluorite' else '橡木'),
                     patrol_result='late_inventory_confirmed', resource_outcome=special_verdict.GATHERED,
                     **evidence.fields())

    def current_target(s):
        if s.get('patrol_context') != context:
            return 'wait', None
        candidates=[];waiting=False
        for kind in required:
            if kind in completed:
                continue
            state,obj=patrol_target(s,kind)
            waiting=waiting or state=='wait'
            if state=='live' and target_attempts[kind]<policy.COORD_PATROL_MAX_TARGET_ATTEMPTS \
                    and not any(k==kind and math.dist(obj['position'],p)<.5 for k,p in rejected):
                first_seen.setdefault(kind,clock.monotonic())
                candidates.append(obj)
        if candidates:
            return 'live', min(candidates,key=lambda o:(first_seen[o['category']],
                    distance(position,o['position']) if position is not None else 0.))
        return ('wait',None) if waiting else ('search',None)

    def navigation_scene():
        # House policy polygons are intentionally not injected. Keep only the
        # scene's existing real collisions and any independently supplied rules.
        return scene_now()

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
                heading_cache.clear()
            clock.sleep(.01)
        return False

    def run_leg(goal, number=None, collect=False, survey_center=None, collect_target=None):
        point = deepcopy(data['points'][0])
        point['position'] = list(goal)
        if collect and collect_target is not None:
            kind=collect_target['category']
            point['special_resource']={'category':kind,'static_id':collect_target['resource_static_id'],
                                       'press_count':cfg['press_count']}
            point['name']='萤石' if kind=='fluorite' else '橡木'
        if not collect:
            point.pop('special_resource')
            point['name'], point['patrol_house'] = f'{number}号房门前', number
        leg = {'schema': 'coordinate-points-v1', 'points': [point], 'patrol_probe': True}
        elapsed, last, was_running = 0., clock.monotonic(), False
        outcome = None
        maintaining = False
        press_started = False
        calibrating = False
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
            if running and not collect and not calibrating and current_target(scene_now())[0] == 'live':
                raise _Found()
            if running and collect and outcome is None and not press_started:
                state, obj = patrol_target(scene_now(), point['special_resource']['category'])
                if state != 'live' or math.dist(obj['position'], goal) > 3.:
                    raise _Lost()
            center = survey_center if survey_center is not None else goal
            if running and not collect and distance(p, center) <= policy.COORD_PATROL_HOUSE_RADIUS \
                    and abs(p[1]-center[1]) <= policy.COORD_MAX_HEIGHT_ERROR:
                raise _Arrived()

        def leg_report(state, message, **extra):
            nonlocal outcome, maintaining, press_started, calibrating
            if state=='calibrating':
                calibrating=True
            elif state=='playing':
                calibrating=False
            if 'resource_outcome' in extra:
                observed = extra['resource_outcome']
                outcome = ('counter_decreased' if observed == special_verdict.GATHERED
                           and extra.get('resource_confirmed') is not True else observed)
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
                           **({'heading_cache':heading_cache} if combined else {}),
                           'navigator': navigator, 'scene_refresh': scene_refresh,
                           'harvest_journal': harvest_journal})
            if not done:
                heading_cache.clear()
            return done, outcome
        except _Arrived:
            return True, None
        finally:
            device.release_all()

    try:
        navigator.scene_provider=navigation_scene
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
        inventory_reader = reader_factory()
        position = tuple(inventory_reader.read())
        if cfg.get('checkpoint_kind')=='house-public-side':
            # Old task snapshots may contain shifted public-side points. Re-read
            # configured entrances rather than silently retaining that policy.
            refreshed=patrol_checkpoints(scene_now())
            if not refreshed:
                raise ValueError('旧巡查任务需要重新读取1～12号房门坐标，请等待主地图同步')
            houses=list(zip(range(1,13),refreshed))
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
                heading_cache.clear()
                unknown_since = None
                continue
            s = scene_now()
            if set(required) <= completed:
                emit('背包增量已确认全部资源，任务结束', state='patrol_finished',
                     patrol_result='gathered', resource_confirmed=True)
                return True
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
                kind=obj['category']
                target_attempts[kind] += 1
                emit('发现实时资源，前往交互范围再次确认', patrol_phase='collect',
                     patrol_goal=list(obj['position']), patrol_source='live-scene')
                try:
                    done, outcome = run_leg(obj['position'], collect=True, collect_target=obj)
                    if done and outcome == special_verdict.GATHERED:
                        completed.add(kind)
                        if set(required)-completed:
                            emit('已确认采到一种资源，继续查找另一种',patrol_result='one_gathered',
                                 resource_outcome=outcome,resource_category=kind)
                            continue
                        emit('已确认采到，任务结束', state='patrol_finished', patrol_result='gathered',
                             resource_outcome=outcome, resource_confirmed=True)
                        return True
                    running, stopped = controls.snapshot()
                    if stopped:
                        return False
                    rejected.append((kind,list(obj['position'])))
                    emit('本次采集结果未确认，避免重复按键，继续房门巡查',
                         patrol_result='unconfirmed', resource_outcome=outcome)
                except (_Expired, _Lost, SpecialTargetUnavailable):
                    rejected.append((kind,list(obj['position'])))
                    emit('目标接近超时或实体失效，继续巡查', patrol_result='unconfirmed')
                continue
            if not pending:
                if failed and not retry_round:
                    pending = [(n, p) for n, p in houses if n in failed]
                    retry_round = True
                    emit('本轮结束，补查未到达的房门一次', patrol_phase='retry')
                    continue
                result = 'partial_resources' if completed else 'unconfirmed' if rejected else 'partial' if failed else 'not_found'
                emit('本轮巡查结束；未确认资源不代表全图没有', state='patrol_finished' if not failed else 'partial',
                     patrol_result=result, patrol_failed=sorted(failed), patrol_attempted=sorted(attempted),
                     resource_confirmed=False)
                return not failed and not rejected and not completed
            number, goal = pending[0]
            emit(f'前往 {number}/12 号房门前', patrol_phase='search', patrol_house=number,
                 patrol_goal=list(goal), patrol_retry=retry_round)
            attempted.add(number)
            try:
                obstacles = ObstacleMap(navigation_scene() or {}, goal, goal=goal, water_areas=navigator.water_areas)
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
        if inventory_reader is not None:
            inventory_reader.close()
        navigator.scene_provider=original_provider
        navigator.invalidate()
