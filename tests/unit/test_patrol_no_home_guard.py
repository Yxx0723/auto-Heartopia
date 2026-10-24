"""Removing a policy restriction must not remove real collision constraints."""
from copy import deepcopy
from tests.unit.test_house_patrol import scene, target, STAMP
from tests.unit.test_coordinate_player import Game
from utils.core.special_patrol import make_patrol_data
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap
from utils.flows.special_patrol import drive_special_patrol


def test_joint_patrol_starts_without_public_side_metadata_or_shifted_checkpoints():
    s=scene()
    d=make_patrol_data(s,[0.,0.,0.],'both','萤石与橡木',3,STAMP)
    assert d['special_patrol']['checkpoint_kind']=='house-entrances'
    assert d['special_patrol']['checkpoints']==[h['position'] for h in s['house_entrances']]


def test_old_policy_polygons_do_not_block_current_position_resource_or_calibration():
    s=scene();s['house_exclusion_state']='ready'
    s['house_exclusions']=[[[-100.,-100.],[200.,-100.],[200.,100.],[-100.,100.]]]
    for h in s['house_entrances']:
        h.update(public_position=[1000.,0.,1000.],public_source='forecourt-public-side-policy')
    s['objects']=[target(position=[0.,0.,0.]),target(category='fluorite',resource_static_id=42002,position=[0.,0.,0.])]
    d=make_patrol_data(s,[0.,0.,0.],'both','萤石与橡木',3,STAMP)
    game=Game();events=[];seen=[];nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    def runner(leg,size,rf,device,window,controls,report,**options):
        seen.append(deepcopy(nav.scene_provider()))
        report('calibrating','direction check')
        report('collecting','confirmed',resource_outcome='gathered',resource_confirmed=True)
        return True
    assert drive_special_patrol(d,game.size(),lambda:game,game,game,game,
        lambda state,text,**extra:events.append((state,text,extra)),clock=game,navigator=nav,point_runner=runner)
    assert len(seen)==2 and all(not x.get('navigation_exclusions') for x in seen)
    assert not any(x[2].get('patrol_result')=='outside_guard' for x in events)
    assert set(events[-1][2]['patrol_completed_resources'])=={'roaming_oak','fluorite'}
    assert not game.keys


def test_real_wall_collision_remains_even_when_house_policy_metadata_is_present():
    s=scene();s['house_exclusions']=[[[-10.,-10.],[10.,-10.],[10.,10.],[-10.,10.]]]
    s['colliders']=[{'polygon':[[2.,-1.],[3.,-1.],[3.,1.],[2.,1.]],'height':[0.,3.]}]
    m=ObstacleMap(s,[0.,0.,0.],water_areas=())
    assert not m.clear((0.,0.),(6.,0.))
    assert m.clear((0.,-4.),(6.,-4.))


def test_legacy_public_side_task_reloads_entrances_instead_of_reusing_shifted_points():
    s=scene();d=make_patrol_data(s,[0.,0.,0.],'both','萤石与橡木',3,STAMP)
    d['special_patrol']['checkpoint_kind']='house-public-side'
    d['special_patrol']['checkpoints']=[[1000.+i,0.,1000.] for i in range(12)]
    game=Game();goals=[]
    def runner(leg,size,rf,device,window,controls,report,**options):
        p=leg['points'][0]['position'];goals.append(p);r=rf()
        try:game.position=list(p);r.read();return True
        finally:r.close();device.release_all()
    nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    assert drive_special_patrol(d,game.size(),lambda:game,game,game,game,lambda *a,**kw:None,
        clock=game,navigator=nav,point_runner=runner)
    assert len(goals)==12
    assert all(abs(goal[0]-row['position'][0])<=2.1 for goal,row in zip(goals,s['house_entrances']))
