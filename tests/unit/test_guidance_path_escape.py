"""Planning failure recovery: recorded footprints and simulated inputs only."""
import json
import math
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_navigation import scene, box
from tests.unit.test_native_guidance_follow import guide_game, run
from utils.core.coordinate_navigation import NavigationWait
from utils.core.guidance_navigation import GuidanceNavigator
from utils.flows.native_guidance_follow import GuidanceFollower


CASE = json.loads(Path(__file__).with_name('guidance_path_obstacle_case.json').read_text(encoding='utf-8'))


def recorded():
    p, target = tuple(CASE['position']), tuple(CASE['local_target'])
    source = scene(*[{'polygon':x['footprint'], 'height':[18.,22.],
                     'source':'recorded-footprint-only'}
                    for x in CASE['navigation_evidence']['rejecting_footprints']])
    return p, target, source


def test_recorded_blocked_connection_can_withdraw_without_crossing_wall():
    p, target, source = recorded()
    nav = GuidanceNavigator(lambda:source, water_areas=(), respect_water=False)
    point = nav.path_escape_target(p, target, 1, p)
    assert point is not None and math.dist(p,point) <= 1.5
    assert nav.obstacles.clear((p[0],p[2]), (point[0],point[2]), escape=True)
    assert nav.obstacles.clear((point[0],point[2]), (point[0],point[2]))
    assert not nav.obstacles.clear((p[0],p[2]), (target[0],target[2]))


def test_no_path_reaches_checked_withdrawal_then_resumes_following():
    game=guide_game();game.stop_at=12.
    nav=GuidanceNavigator(lambda:scene(),clock=game.monotonic,water_areas=())
    original=nav.next_target
    def blocked(p,target,*a,**kw):
        if not any(e['key']=='s' and e['type']=='key_down' for _,e in game.events):
            raise NavigationWait('no local connection')
        return original(p,target,*a,**kw)
    nav.next_target=blocked
    records=[]
    result=run(game,navigator=nav,report=lambda s,m,**kw:records.append((s,kw)))
    assert result['arrived']
    assert any(kw.get('recovery_phase')=='path_escape' for _,kw in records)
    assert not any(s=='paused' for s,_ in records)
    assert not game.keys and game.closed
    assert not any(e['key'] in ('space','f','b','t') for _,e in game.events)


def test_no_safe_withdrawal_never_sends_movement_or_learns_a_wall():
    game=guide_game();game.stop_at=8.
    nav=GuidanceNavigator(lambda:scene(box(-2,2,-2,2)),clock=game.monotonic,water_areas=())
    nav.blocked=Mock()
    records=[]
    run(game,navigator=nav,report=lambda s,m,**kw:records.append(kw))
    assert any(x.get('recovery_phase')=='path_escape' for x in records)
    assert not game.events and not game.keys
    nav.blocked.assert_not_called()


def follower(game,source):
    nav=GuidanceNavigator(lambda:source[0],clock=game.monotonic,water_areas=())
    f=GuidanceFollower(game.size(),game,game,game,game,Mock(),game,nav)
    f.read();f.refresh_guidance(f.position)
    f.read_direction();game.sleep(.11);f.read_direction()
    f.waiting.failed('path',game.time)
    game.sleep(3.01)
    return f,nav


def test_attempt_budget_is_not_reset_by_geometry_refresh_or_one_successful_plan():
    game=guide_game();source=[scene()];f,nav=follower(game,source)
    nav.path_escape_target=Mock(return_value=None)
    for i in range(3):
        f.try_path_escape(f.position,(0,0,1.2))
        source[0]=scene();nav.invalidate();game.sleep(3.01)
    assert f.path_escape_attempts==3
    assert not game.events
    f.input_lease.close()


@pytest.mark.parametrize('fault',['stop','focus','scene','target'])
def test_withdrawal_preserves_stop_focus_scene_and_target_protection(fault):
    from utils.flows.coordinate_player import _Stopped, _Paused
    from utils.flows.native_guidance_follow import _GuidanceEnded, _GuidanceRetry
    game=guide_game();source=[scene()];f,nav=follower(game,source)
    def interrupt(event):
        if event['type']!='key_down':return
        if fault=='stop':game.stop_at=game.time
        elif fault=='focus':game.focus=False
        elif fault=='scene':source[0]=None
        else:f.source=lambda:{'state':'inactive'}
    game.on_input=interrupt
    try:
        with pytest.raises((_Stopped,_Paused,NavigationWait,_GuidanceEnded,_GuidanceRetry)):
            f.try_path_escape(f.position,(0,0,1.2))
    finally:
        f.input_lease.close()
    assert not game.keys
    downs=[(t,e) for t,e in game.events if e['type']=='key_down']
    assert len(downs)==1


def test_new_live_entity_wall_blocks_withdrawal_even_when_terrain_guard_is_empty():
    source=[scene()]
    nav=GuidanceNavigator(lambda:source[0],water_areas=())
    p=(0,0,0);goal=nav.path_escape_target(p,(0,0,2),1,p)
    assert goal is not None
    source[0]=scene(box(-2,2,-2,2))
    basis={'forward':(0,1),'right':(1,0)}
    context=(nav.scene.get('cache_context'),nav.scene.get('terrain_revision'))
    assert not nav.path_escape_keys(p,goal,basis,.48,context)


def test_changed_world_cannot_reuse_withdrawal_permission():
    source=[{**scene(),'cache_context':('game','world-a')}]
    nav=GuidanceNavigator(lambda:source[0],water_areas=())
    p=(0,0,0);goal=nav.path_escape_target(p,(0,0,2),1,p)
    source[0]={**scene(),'cache_context':('game','world-b')}
    with pytest.raises(NavigationWait) as caught:
        nav.path_escape_keys(p,goal,{'forward':(0,1),'right':(1,0)},.48,(('game','world-a'),None))
    assert caught.value.reason=='scene'


def test_does_not_use_the_old_terrain_only_recovery_guard():
    game=guide_game();source=[scene()];f,nav=follower(game,source)
    nav.recovery_keys=Mock(side_effect=AssertionError('terrain-only guard must not be used'))
    try:assert f.try_path_escape(f.position,(0,0,1.2))
    finally:f.input_lease.close()
    nav.recovery_keys.assert_not_called()
    assert game.position[2]<0 and not game.keys


def test_slow_read_during_withdrawal_is_still_released_by_watchdog():
    from utils.flows.native_guidance_follow import _GuidanceRetry
    game=guide_game();source=[scene()];f,nav=follower(game,source)
    original=game.read;delayed=[]
    def read():
        if game.keys and not delayed:
            delayed.append(True);game.time+=.3;f.input_lease.poll()
        return original()
    game.read=read
    try:
        with pytest.raises(_GuidanceRetry):f.try_path_escape(f.position,(0,0,1.2))
        assert f.input_lease.metrics()['release_counts']['lease_expired']==1
    finally:f.input_lease.close()
    assert not game.keys


def test_withdrawal_budget_and_episode_radius_remain_bounded():
    from utils.flows.coordinate_player import _Paused
    game=guide_game();source=[scene()];f,nav=follower(game,source)
    f.path_escape_attempts=f.recovery_attempts
    try:
        with pytest.raises(_Paused):f.try_path_escape(f.position,(0,0,1.2))
    finally:f.input_lease.close()
    assert not game.events
    nav=GuidanceNavigator(lambda:scene(),water_areas=())
    # Already at the bounded episode perimeter: every returned endpoint remains inside.
    p=(0,0,-2.7);point=nav.path_escape_target(p,(0,0,2),1,(0,0,0))
    assert point is None or math.dist(point,(0,0,0)) <= 2.7


def test_projection_checks_do_not_fake_route_progress():
    game=guide_game();source=[scene()];f,nav=follower(game,source)
    before=f.cursor.arc
    try:assert f.try_path_escape(f.position,(0,0,1.2))
    finally:f.input_lease.close()
    assert f.cursor.arc==before  # A retreat is not forward traversal evidence.


def test_recorded_actual_polygon_overlap_never_authorizes_an_exit_through_wall():
    p,target,source=recorded()
    poly=source['colliders'][2]['polygon']
    center=(sum(v[0] for v in poly)/len(poly),p[1],sum(v[1] for v in poly)/len(poly))
    nav=GuidanceNavigator(lambda:source,water_areas=())
    assert nav.path_escape_target(center,target,1,center) is None


def test_recorded_withdrawal_does_not_stop_early_inside_artificial_padding():
    from tests.unit.test_native_guidance_follow import snapshot
    from utils.core.coordinate_navigation import ObstacleMap
    p,target,geometry=recorded();game=guide_game();game.position=list(p)
    dx,dz=target[0]-p[0],target[2]-p[2]
    path=(tuple([p[0],p[1]+.5,p[2]]),(p[0]+dx*6,p[1]+.5,p[2]+dz*6))
    game.guidance=lambda:snapshot(path=path,target=path[-1],now=game.time)
    f,nav=follower(game,[geometry])
    try:assert f.try_path_escape(p,target)
    finally:f.input_lease.close()
    assert ObstacleMap(geometry,p,water_areas=()).clear(
        (game.position[0],game.position[2]),(game.position[0],game.position[2]))


def test_persistent_no_path_has_bounded_checks_and_an_explained_pause():
    game=guide_game();game.stop_at=18.
    nav=GuidanceNavigator(lambda:scene(box(-2,2,-2,2)),clock=game.monotonic,water_areas=())
    reports=[]
    run(game,navigator=nav,report=lambda s,m,**kw:reports.append((s,m,kw)))
    paused=[x for x in reports if x[0]=='paused']
    assert paused and any(x[2].get('recovery_result')=='no_connection' for x in paused)
    checks=[x for x in reports if x[2].get('recovery_phase')=='path_escape'
            and 'recovery_attempt' in x[2]]
    assert max(x[2]['recovery_attempt'] for x in checks)<=5
    assert not game.events
