"""Native approach and exact harvest stay separate, with simulated movement."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.unit.test_coordinate_player import Game,manual_points
from tests.unit.test_coordinate_navigation import scene
from utils.core.collection_navigation import CollectionNavigator
from utils.flows.coordinate_player import drive_points
from utils.flows.native_collection import BoundGuidanceReader


def install_fake_map(monkeypatch,game):
    calls=[];game.movement_basis=lambda:{'forward':[0.,1.],'right':[1.,0.]}
    state={}
    def read():
        return {**deepcopy(state['route']),'captured_at':game.time}
    game.guidance=read
    class Targeter:
        def __init__(self,*args,**kwargs):
            self.panel=SimpleNamespace(scene_context=lambda:'world-a')
            self.options=SimpleNamespace(world_tolerance=.75)
        def set_target(self,target):
            calls.append(tuple(target))
            route=dict(state='ready',context='module-a',scene_context='world-a',tracking_id='1234',
                target=tuple(target),path=(tuple(game.position),tuple(target)),captured_at=game.time)
            state['route']=route
            return {'route':route,'tracking_id':'1234'}
    monkeypatch.setattr('utils.flows.native_collection.MapTargeter',Targeter)
    return calls


def run(game,positions,report=lambda *a,**k:None,**kwargs):
    nav=CollectionNavigator(lambda:scene(),clock=game.monotonic,scene_clock=game.monotonic,
                            water_areas=(),native_map_collection=True)
    data=manual_points(positions);before=deepcopy(data)
    result=drive_points(data,game.size(),lambda:game,game,game,game,report,clock=game,
                        navigator=nav,collect_wait_seconds=.1,**kwargs)
    assert data==before and not game.keys and game.closed
    return result


def test_native_guidance_each_point_then_original_collection_wait_and_loops(monkeypatch):
    game=Game();calls=install_fake_map(monkeypatch,game);reached=[]
    def report(state,message,**kw):
        if state=='reached':reached.append((kw['cycle'],kw['waypoint']))
    assert run(game,[(0,0,5),(4,0,5)],report,loop_count=2)
    assert reached==[(1,1),(1,2),(2,1),(2,2)]
    assert calls==[(0.,0.,5.),(4.,0.,5.),(0.,0.,5.),(4.,0.,5.)]
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==4


def test_already_close_point_needs_only_exact_approach_not_a_new_map_marker(monkeypatch):
    game=Game();calls=install_fake_map(monkeypatch,game)
    assert run(game,[(0,0,1)])
    assert not calls
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==1


def test_guidance_end_far_away_is_not_collection_arrival_or_a_long_direct_fallback(monkeypatch):
    game=Game();install_fake_map(monkeypatch,game)
    monkeypatch.setattr('utils.flows.native_collection.GuidanceFollower.follow_guidance',
        lambda self:dict(arrived=False,reason='游戏追踪已结束，已停止跟随'))
    with pytest.raises(ValueError,match='仍离采集点过远'):run(game,[(0,0,50)])
    assert not any(e.get('key')=='f' for _,e in game.events)
    assert not game.keys and game.closed


def test_failed_guidance_does_not_press_f_even_when_inside_local_finish_radius(monkeypatch):
    game=Game();install_fake_map(monkeypatch,game)
    monkeypatch.setattr('utils.flows.native_collection.GuidanceFollower.follow_guidance',
        lambda self:dict(arrived=False,reason='failed'))
    with pytest.raises(ValueError,match='游戏引导未完成'):run(game,[(0,0,5)])
    assert not any(e.get('key')=='f' for _,e in game.events)


def test_f9_during_guidance_releases_and_never_harvests(monkeypatch):
    game=Game();game.stop_at=.9;install_fake_map(monkeypatch,game)
    assert not run(game,[(0,0,30)])
    assert not any(e.get('key')=='f' for _,e in game.events)


@pytest.mark.parametrize('fault',['scene','id','target','stale'])
def test_saved_point_binding_rejects_other_native_targets(fault):
    value=dict(state='ready',context='module-a',scene_context='world-a',tracking_id='1234',
               target=(10.,0.,20.),captured_at=1.)
    original=deepcopy(value)
    if fault=='scene':value['scene_context']='other'
    elif fault=='id':value['tracking_id']='4567'
    elif fault=='target':value['target']=(15.,0.,20.)
    else:value['captured_at']=-10.
    reader=SimpleNamespace(guidance=lambda:value)
    bound=BoundGuidanceReader(reader,original,(10.,5.,20.),lambda:'world-a',lambda:1.,.75)
    with pytest.raises(ValueError):bound.guidance()


def test_inactive_guidance_still_checks_world_identity():
    reader=SimpleNamespace(guidance=lambda:{'state':'inactive'})
    prepared={'scene_context':'world-a','tracking_id':'1234'}
    bound=BoundGuidanceReader(reader,prepared,(0.,0.,0.),lambda:'world-b',lambda:1.,.75)
    with pytest.raises(ValueError,match='场景'):bound.guidance()


def test_long_native_leg_checks_collection_maintenance_and_releases_before_menu(monkeypatch):
    from utils.flows.native_collection import NativeCollectionFollower
    game=Game();install_fake_map(monkeypatch,game);checked=[];used=[]
    def maintain(self,index,total):
        if getattr(self,'native_follower',None) is None:return False
        checked.append(game.time)
        if game.time>1. and not used:
            self.release('test_maintenance')
            assert not game.keys and self.native_follower.input_lease.deadline is None
            game.sleep(.5);used.append(True)
            return True
        return False
    monkeypatch.setattr(NativeCollectionFollower,'maintain_stamina',maintain)
    assert run(game,[(0,0,10)])
    assert len(checked)>1 and used==[True]
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==1
