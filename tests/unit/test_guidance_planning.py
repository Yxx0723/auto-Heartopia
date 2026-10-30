"""Guidance planning regression; only simulated input and clocks."""
from unittest.mock import Mock
from tests.unit.test_guidance_route_cache import cached_game
from tests.unit.test_native_guidance_follow import run
from tests.unit.test_coordinate_navigation import scene
from utils.core.guidance_navigation import GuidanceNavigator


def test_one_call_refreshes_obstacles_only_once():
    source=scene();nav=GuidanceNavigator(lambda:source,water_areas=())
    from utils.core.coordinate_navigation import LocalNavigator
    original=LocalNavigator._refresh_obstacles
    called=[]
    def refresh(self,*a,**k):
        called.append(1);return original(self,*a,**k)
    from unittest.mock import patch
    with patch.object(LocalNavigator,'_refresh_obstacles',refresh):
        nav.next_target((0,0,0),(0,0,6))
    assert len(called)==1


def test_forward_aim_motion_does_not_replan_each_control_tick():
    game=cached_game();game.stop_at=.65;source=scene()
    nav=GuidanceNavigator(lambda:source,clock=game.monotonic,water_areas=())
    run(game,navigator=nav)
    assert nav.stats['plans']<=3
    assert not game.keys


import pytest
from utils.core.guidance_anchor import GuidanceAnchor
from utils.core.guidance_path import GuidanceCursor, validate_guidance
from tests.unit.test_native_guidance_follow import snapshot, guide_game
from tests.unit.test_coordinate_navigation import box


def cursor(path=((0,.5,0),(0,.5,20))):
    return GuidanceCursor(validate_guidance(snapshot(path=path,target=path[-1]),0.),(0,0,0))


def test_anchor_stable_on_straight_line_and_advances_near_it():
    c=cursor();a=GuidanceAnchor()
    point=a.choose(c,(0,0,0),c.aim((0,0,0)))
    assert point==(0,0,3)
    for z in (.1,.5,1.,2.):
        p=(0,0,z);assert a.choose(c,p,c.aim(p))==point
    p=(0,0,2.5);assert a.choose(c,p,c.aim(p))[2]>point[2]


def test_anchor_keeps_short_aim_at_sharp_bend():
    c=cursor(((0,.5,0),(0,.5,1),(3,.5,1)))
    p=(0,0,0);aim=c.aim(p)
    assert GuidanceAnchor().choose(c,p,aim)==aim['position']


def test_anchor_does_not_change_cursor_arrival_or_target():
    c=cursor();before=(c.path,c.target,c.revision);a=GuidanceAnchor()
    a.choose(c,(0,0,0),c.aim((0,0,0)))
    assert (c.path,c.target,c.revision)==before
    assert not c.aim((0,0,3))['arrived']
    assert c.aim((0,0,19.8))['arrived']


def test_anchor_changes_on_route_revision_and_rejects_large_height_hint():
    c=cursor();a=GuidanceAnchor();a.choose(c,(0,0,0),c.aim((0,0,0)))
    other=cursor(((0,.5,0),(10,.5,0)))
    p=(0,0,0);assert a.choose(other,p,other.aim(p))[0]==3.
    slope=cursor(((0,.5,0),(0,4,3),(0,4,5)))
    aim=slope.aim(p);assert a.choose(slope,p,aim)==aim['position']


def test_small_height_noise_keeps_anchor_but_layer_refresh_resets_it():
    c=cursor();a=GuidanceAnchor();p=(0,0,0);first=a.choose(c,p,c.aim(p))
    p=(0,.1,.1);assert a.choose(c,p,c.aim(p))==first
    p=(0,.8,.2);assert a.choose(c,p,c.aim(p))[1]==.8


def test_failed_anchor_falls_back_without_repeatedly_retrying_same_far_point():
    c=cursor();a=GuidanceAnchor();p=(0,0,0);a.choose(c,p,c.aim(p));a.reject(c)
    p=(0,0,.2);aim=c.aim(p)
    assert a.choose(c,p,aim)==aim['position']


def test_follow_target_uses_original_short_aim_when_far_anchor_fails():
    from utils.core.coordinate_navigation import NavigationWait
    c=cursor();nav=GuidanceNavigator(lambda:scene(),water_areas=())
    nav.next_target=Mock(side_effect=[NavigationWait('far unavailable'),(0,0,1.2)])
    aim=c.aim((0,0,0))
    assert nav.follow_target(c,(0,0,0),aim,lambda:None,lambda:None)==aim['position']
    assert nav.next_target.call_count==2 and nav.anchor.point is None


def test_new_wall_cannot_be_bypassed_by_reused_anchor_or_refresh_ticket():
    c=cursor();source=[scene()];nav=GuidanceNavigator(lambda:source[0],water_areas=())
    p=(0,0,0);aim=c.aim(p)
    nav.follow_target(c,p,aim,lambda:None,lambda:None)
    source[0]=scene(box(-.5,.5,1,1.2));released=[]
    point=nav.follow_target(c,p,aim,lambda:None,lambda:released.append(1))
    assert released and not nav.obstacles.clear((0,0),(0,3))
    assert point[0]!=0


def test_refresh_ticket_is_never_reused_across_calls_and_reports_timings():
    source=scene();nav=GuidanceNavigator(lambda:source,water_areas=())
    for _ in range(2):
        nav.next_target((0,0,0),(0,0,6))
        assert nav.last_timings['refresh_calls']==1
        assert nav.last_timings['refresh_reuses']==1
        assert nav.last_timings['total_seconds']>=nav.last_timings['shore_seconds']
    assert not nav._in_call and nav._refresh_ticket is None


def test_scene_loss_cannot_reuse_last_call_ticket():
    from utils.core.coordinate_navigation import NavigationWait
    source=[scene()];nav=GuidanceNavigator(lambda:source[0],water_areas=())
    nav.next_target((0,0,0),(0,0,6));source[0]=None
    with pytest.raises(NavigationWait) as ex:nav.next_target((0,0,0),(0,0,6))
    assert ex.value.reason=='scene' and nav._refresh_ticket is None


def test_search_and_map_release_diagnostics_are_distinct():
    source=scene(box());nav=GuidanceNavigator(lambda:source,water_areas=());reasons=[]
    nav.next_target((0,0,0),(0,0,6),before_plan=lambda:reasons.append(nav.release_reason))
    assert 'map_build' in reasons and 'path_search' in reasons


def test_independent_release_and_controller_cleanup_are_counted_once():
    from utils.drivers.guidance_input_lease import GuidanceInputLease
    game=guide_game();lease=GuidanceInputLease(game,game,game,game.size(),game.monotonic)
    game.keys={'w'};lease.arm(.1);lease.observe_keys({'w'})
    game.time=.2;lease.poll();lease.release('controller_cleanup',active=True)
    game.time=.4;game.keys={'w'};lease.observe_keys({'w'})
    metrics=lease.metrics()
    assert metrics['release_counts']=={'lease_expired':1}
    assert metrics['resumed_gap_count']==1
    assert metrics['resumed_gap_total_seconds']==pytest.approx(.2)
    assert metrics['recent_input_gaps'][0]['reason']=='lease_expired'
    lease.close()


def test_steering_empty_keys_is_measured_without_claiming_multiple_gaps():
    from utils.drivers.guidance_input_lease import GuidanceInputLease
    game=guide_game();lease=GuidanceInputLease(game,game,game,game.size(),game.monotonic)
    lease.observe_keys({'w'});game.time=.1;lease.observe_keys(set());game.time=.2;lease.observe_keys(set())
    game.time=.3;lease.observe_keys({'w'})
    m=lease.metrics();assert m['release_counts']=={'steering_idle':1}
    assert m['longest_resumed_gap_seconds']==pytest.approx(.2)
    lease.close()


def test_risk_revision_change_inside_call_prevents_refresh_reuse():
    nav=GuidanceNavigator(lambda:scene(),water_areas=())
    def change(*a):
        nav.risks.revision+=1
        return None
    nav._shore_target=change
    nav.next_target((0,0,0),(0,0,6))
    assert nav.last_timings['refresh_calls']==2


def test_exception_does_not_leave_call_local_refresh_ticket_active():
    nav=GuidanceNavigator(lambda:scene(),water_areas=())
    def stop():raise RuntimeError('F9')
    with pytest.raises(RuntimeError,match='F9'):
        nav.next_target((0,0,0),(0,0,6),check=stop)
    assert not nav._in_call and nav._refresh_ticket is None
    assert nav.next_target((0,0,0),(0,0,6))
