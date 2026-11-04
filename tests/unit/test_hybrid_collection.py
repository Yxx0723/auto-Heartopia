"""Bounded supplementation and hybrid entry; no live game or map input."""
from unittest.mock import Mock
import pytest
from tests.unit.test_collection_continuity import game_and_nav
from tests.unit.test_coordinate_player import manual_points
from utils.flows.coordinate_player import drive_points


def test_default_collection_worker_does_not_select_native_map_entry():
    from utils.ui.point_playback import PointPlayback
    from tests.unit.test_point_playback import FakeWindow,WaitingControls,points
    seen=[]
    def runner(*args,**kwargs):
        seen.append(kwargs['navigator']);return True
    worker=PointPlayback('profile',scene_provider=Mock(),runner=runner,window_factory=FakeWindow,
        controls_factory=WaitingControls,device_factory=lambda w:Mock())
    worker.start(points(),77,auto_navigate=True,remember_paths=False)
    worker.thread.join(2)
    assert len(seen)==1 and seen[0].native_map_collection is False
    worker.close()


def test_skipped_point_is_retried_once_after_remaining_points_and_then_resolved():
    from utils.flows.coordinate_player import _SkipPoint
    game,nav=game_and_nav();failed=[];reached=[]
    original=nav.next_target
    def next_target(position,target,**kw):
        if tuple(target)==(0,0,4) and not failed:
            failed.append(True);raise _SkipPoint('temporary blocked')
        return original(position,target,**kw)
    nav.next_target=next_target
    def report(state,message,**kw):
        if state=='reached':reached.append(kw['waypoint'])
    assert drive_points(manual_points([(0,0,4),(3,0,4)]),game.size(),lambda:game,game,game,game,
                        report,clock=game,navigator=nav,collect_wait_seconds=0.)
    assert reached==[2,1] and len(failed)==1 and not game.keys


def test_permanently_unreachable_point_is_only_attempted_twice():
    from utils.flows.coordinate_player import _SkipPoint
    game,nav=game_and_nav();calls=[];report=Mock()
    def no_path(position,target,**kw):
        calls.append(target);raise _SkipPoint('wall')
    nav.next_target=no_path
    assert not drive_points(manual_points([(0,0,4)]),game.size(),lambda:game,game,game,game,
                            report,clock=game,navigator=nav,collect_wait_seconds=0.)
    assert len(calls)==2 and not game.keys


def test_memory_can_choose_a_later_verified_entry_instead_of_blocked_nearest(tmp_path):
    from utils.drivers.route_path_memory import RoutePathMemory
    m=RoutePathMemory(tmp_path/'test.json',manual_points([(0,0,8)]))
    path=[[0,0,i] for i in range(9)]
    assert m.remember(0,path)
    assert m.lookup(0,(0,0,0),can_connect=lambda p:p[2]>=2)[0][2]>=2


def test_blocked_memory_entry_only_rejoins_forward_and_does_not_delete_trace(tmp_path):
    from utils.drivers.route_path_memory import RoutePathMemory
    m=RoutePathMemory(tmp_path/'test.json',manual_points([(0,0,8)]))
    assert m.remember(0,[[0,0,i] for i in range(9)])
    assert m.lookup(0,(0,0,0))
    m.reject_selected(position=(0,0,2))
    result=m.lookup(0,(0,0,2),can_connect=lambda p:True)
    assert result and result[0][2]>2 and m.legs


@pytest.mark.parametrize('loop',[True,False])
def test_progress_detects_repeated_loops_without_rejecting_straight_travel(loop):
    from utils.core.navigation_progress import NavigationProgress
    p=NavigationProgress();p.bind('point',(0,0,0),(30,0,0),3)
    detected=False
    for i in range(32):
        position=([(0,0,0),(2,0,0),(2,0,2),(0,0,2)][i%4] if loop else (i,0,0))
        p.account(.5);detected=detected or p.looping(position)
    assert detected is loop


def test_scene_wait_is_bounded_and_not_learned_as_a_wall(monkeypatch):
    import utils.flows.coordinate_player as module
    from utils.core.coordinate_navigation import NavigationWait
    monkeypatch.setattr(module,'SCENE_WAIT_SECONDS',.3)
    game,nav=game_and_nav();nav.next_target=Mock(side_effect=NavigationWait('loading',reason='scene'))
    reports=[]
    assert not drive_points(manual_points([(0,0,4)]),game.size(),lambda:game,game,game,game,
                            lambda s,m,**kw:reports.append((s,m)),clock=game,navigator=nav)
    assert sum(s=='skipped' for s,_ in reports)==2 and nav.risks.count==0
    assert not game.keys


def test_near_xz_other_height_is_not_a_memory_entry(tmp_path):
    from utils.drivers.route_path_memory import RoutePathMemory
    m=RoutePathMemory(tmp_path/'test.json',manual_points([(0,0,8)]))
    assert m.remember(0,[[0,.6,i] for i in range(8)]+[[0,0,8]])
    assert not m.lookup(0,(0,0,0),can_connect=lambda p:True)


def test_two_cycles_have_separate_bounded_supplement_passes():
    from utils.flows.coordinate_player import _SkipPoint
    game,nav=game_and_nav();attempts=[]
    def no_path(position,target,**kw):
        attempts.append(tuple(target));raise _SkipPoint('wall')
    nav.next_target=no_path
    assert not drive_points(manual_points([(0,0,4)]),game.size(),lambda:game,game,game,game,
                            Mock(),clock=game,navigator=nav,loop_count=2)
    assert len(attempts)==4 and not game.keys


def test_partial_memory_looks_ahead_over_contiguous_samples_and_still_learns_full_route(tmp_path):
    from utils.drivers.route_path_memory import RoutePathMemory
    game,nav=game_and_nav()
    data=manual_points([(0,0,8)])
    memory=RoutePathMemory(tmp_path/'test.json',data);nav.path_memory=memory
    library=Mock()
    library.candidates.return_value=[{'start':[0,0,i*.25],'end':[0,0,(i+1)*.25]}
                                    for i in range(32)]
    game.traversal_memory=library
    assert drive_points(data,game.size(),lambda:game,game,game,game,Mock(),clock=game,navigator=nav)
    assert memory.count==1
    forward=[e for _,e in game.events if e=={'type':'key_down','key':'w'}]
    assert len(forward)<5


def test_scene_gap_keeps_failed_entry_but_real_world_change_clears_it(tmp_path):
    from utils.drivers.route_path_memory import RoutePathMemory
    from utils.core.coordinate_navigation import NavigationWait
    from tests.unit.test_coordinate_navigation import scene
    game,nav=game_and_nav()
    m=RoutePathMemory(tmp_path/'test.json',manual_points([(0,0,8)]))
    assert m.remember(0,[[0,0,i] for i in range(9)])
    source=[dict(scene(),cache_context='a')];nav.scene_provider=lambda:source[0]
    nav.path_memory=m;nav.set_waypoint(0)
    nav.next_target((0,0,0),(0,0,8));nav.blocked((0,0,2),(0,0,4))
    assert m.blocked_entries
    source[0]=None
    with pytest.raises(NavigationWait):nav.next_target((0,0,2),(0,0,8))
    assert m.blocked_entries
    source[0]=dict(scene(),cache_context='b')
    nav.next_target((0,0,2),(0,0,8))
    assert not m.blocked_entries


def test_failed_memory_prefix_expires_instead_of_becoming_permanent(tmp_path,monkeypatch):
    import utils.drivers.route_path_memory as module
    clock=[0.];monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    m=module.RoutePathMemory(tmp_path/'test.json',manual_points([(0,0,8)]))
    assert m.remember(0,[[0,0,i] for i in range(9)])
    m.lookup(0,(0,0,0));m.reject_selected(position=(0,0,2))
    assert m.lookup(0,(0,0,2),can_connect=lambda p:True)[0][2]>2
    clock[0]=121.
    assert m.lookup(0,(0,0,2),can_connect=lambda p:True)[0][2]==2


def test_stop_at_supplement_start_does_not_press_collection_key():
    from utils.flows.coordinate_player import _SkipPoint
    game,nav=game_and_nav()
    def fail(*args,**kwargs):raise _SkipPoint('blocked')
    nav.next_target=fail
    def report(state,message,**kwargs):
        if state=='supplementing':game.stop_at=game.time
    assert not drive_points(manual_points([(0,0,4)]),game.size(),lambda:game,game,game,game,
                            report,clock=game,navigator=nav,collect_wait_seconds=0.)
    assert not game.keys and not any(e=={'type':'key_down','key':'f'} for _,e in game.events)
