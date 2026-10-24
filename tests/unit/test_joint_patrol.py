"""Joint discovery and nonphysical homeland exclusions apply to every nav edge."""
from unittest.mock import Mock
import pytest

from tests.unit.test_house_patrol import scene as base_scene, target, STAMP
from tests.unit.test_coordinate_player import Game
from utils.core.special_patrol import make_patrol_data, patrol_target
from utils.core.coordinate_navigation import ObstacleMap, LocalNavigator, scene_geometry_signature
from utils.flows.special_patrol import drive_special_patrol


def scene():
    s=base_scene()
    s['house_exclusion_state']='ready'
    s['house_exclusions']=[[[200.,200.],[210.,200.],[210.,210.],[200.,210.]]]
    for row in s['house_entrances']:
        row.update(public_position=list(row['position']),public_source='forecourt-public-side-policy')
    return s


def joint(s):
    return make_patrol_data(s, [0.,0.,0.], 'both', '萤石与橡木', 3, STAMP)


def test_combined_task_validates_without_fabricating_one_resource_identity():
    d = joint(scene())
    assert d['special_patrol']['categories'] == ['fluorite','roaming_oak']


def test_simultaneously_loaded_resources_are_not_ambiguity_between_species():
    s=scene();s['objects']=[target(),target(category='fluorite',resource_static_id=42002,position=[5.,0.,0.])]
    state,obj=patrol_target(s,'both')
    assert state=='live' and obj['category']=='roaming_oak'


def test_joint_task_collects_first_discovered_then_other_without_recollecting_first():
    s=scene();oak=target();s['objects']=[oak]
    game=Game();collected=[];events=[]
    def runner(d,size,rf,device,window,controls,report,**kw):
        point=d['points'][0];r=rf()
        try:
            if 'special_resource' in point:
                category=point['special_resource']['category'];collected.append(category)
                report('collecting','confirmed',resource_outcome='gathered',resource_confirmed=True)
                if category=='roaming_oak':s['objects'].append(target(category='fluorite',resource_static_id=42002))
                return True
            game.position=point['position'];r.read();return True
        finally:r.close();device.release_all()
    nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    assert drive_special_patrol(joint(s),game.size(),lambda:game,game,game,game,
        lambda st,tx,**kw:events.append(kw),clock=game,navigator=nav,point_runner=runner)
    assert collected==['roaming_oak','fluorite']
    assert set(events[-1]['patrol_completed_resources'])=={'roaming_oak','fluorite'}


def test_homeland_exclusion_is_hard_even_with_goal_padding_and_height_changes():
    polygon=[[2.,-2.],[6.,-2.],[6.,2.],[2.,2.]]
    s=scene();s['navigation_exclusions']=[polygon]
    for y in (0.,20.,100.):
        m=ObstacleMap(s,[0.,y,0.],water_areas=(),goal=[3.,y,0.])
        assert not m.clear((0.,0.),(8.,0.))
        assert not m.clear((0.,0.),(3.,0.))
        assert m.clear((0.,-5.),(8.,-5.))
    assert scene_geometry_signature(s)!=scene_geometry_signature(scene())


def test_astar_and_shortcuts_detour_around_homeland_without_weakening_real_walls():
    s=scene();s['navigation_exclusions']=[[[2.,-2.],[6.,-2.],[6.,2.],[2.,2.]]]
    m=ObstacleMap(s,[-2.,0.,0.],water_areas=())
    nav=LocalNavigator(lambda:s,water_areas=())
    next_point=nav.next_target([-2.,0.,0.],[10.,0.,0.])
    assert m.clear((-2.,0.),(next_point[0],next_point[2]))
    assert not m.clear((0.,0.),(8.,0.))


@pytest.mark.parametrize('fault',['unavailable','no_polygons','missing_public_point'])
def test_joint_start_ignores_disabled_or_missing_public_side_data(fault):
    s=scene()
    if fault=='unavailable':s['house_exclusion_state']='unavailable'
    if fault=='no_polygons':s['house_exclusions']=[]
    if fault=='missing_public_point':s['house_entrances'][0].pop('public_position')
    assert joint(s)['special_patrol']['checkpoint_kind']=='house-entrances'


def test_old_public_side_polygon_does_not_force_patrol_to_stop():
    s=scene();game=Game();s['house_exclusions']=[[[ -5.,-5.],[5.,-5.],[5.,5.],[-5.,5.]]]
    s['objects']=[target(),target(category='fluorite',resource_static_id=42002)]
    def runner(data,size,rf,device,window,controls,report,**options):
        report('collecting','confirmed',resource_outcome='gathered',resource_confirmed=True)
        return True
    events=[];nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    original=nav.scene_provider
    assert drive_special_patrol(joint(s),game.size(),lambda:game,game,game,game,
        lambda st,tx,**kw:events.append(kw),clock=game,navigator=nav,point_runner=runner)
    assert events[-1]['patrol_result']=='gathered'
    assert nav.scene_provider is original and not game.events


def test_one_resource_success_does_not_claim_two_species_complete():
    s=scene();s['objects']=[target()];game=Game();collected=[];events=[]
    def runner(d,size,rf,device,window,controls,report,**kw):
        p=d['points'][0];r=rf()
        try:
            if 'special_resource' in p:
                collected.append(p['special_resource']['category']);report('collecting','ok',resource_outcome='gathered',resource_confirmed=True)
            else:game.position=p['position'];r.read()
            return True
        finally:r.close();device.release_all()
    nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    assert not drive_special_patrol(joint(s),game.size(),lambda:game,game,game,game,
        lambda st,tx,**kw:events.append(kw),clock=game,navigator=nav,point_runner=runner)
    assert collected==['roaming_oak']
    assert events[-1]['patrol_result']=='partial_resources'
    assert events[-1]['patrol_checked']==12


def test_public_policy_does_not_mistake_whole_forecourt_for_home_interior():
    from utils.core.patrol_public_side import exclusion_polygon,public_clear,public_checkpoint
    poly,direction=exclusion_polygon([0.,0.,0.],[9.,0.,0.],
        [[4.,0.,-8.],[12.,0.,-8.],[12.,0.,8.],[4.,0.,8.]],[-100.,-100.,100.,100.])
    assert public_clear([9.,0.,0.],[poly])
    assert not public_clear([0.,0.,0.],[poly])
    assert not public_clear([5.,0.,0.],[poly])  # safety buffer toward the house-facing edge
    p=public_checkpoint([9.,0.,0.],direction,[poly])
    assert public_clear(p,[poly],4.5)


def test_ephemeral_heading_avoids_repeated_calibration_beside_house():
    from utils.flows.coordinate_player import _Follower
    game=Game();s=scene()
    nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    point={'name':'public road','position':[4.,0.,0.],'wait':0.}
    f=_Follower({'duration':100.,'client_size':game.size()},game,game,game,game,Mock(),game,
                waypoints=[point],navigator=nav)
    f.heading_cache={'basis':{'forward':[0.,1.],'right':[1.,0.]}}
    f.calibrate=Mock(side_effect=AssertionError('Valid same-task heading should be reused'))
    assert f.follow()
    f.calibrate.assert_not_called()
    assert not game.keys


def test_new_guard_snapshot_invalidates_old_straight_shortcut():
    snapshots=[scene()]
    nav=LocalNavigator(lambda:snapshots[0],water_areas=())
    nav.next_target([-2.,0.,0.],[10.,0.,0.])
    signature=nav.scene_signature
    snapshots[0]={**snapshots[0],'navigation_exclusions':[[[2.,-2.],[6.,-2.],[6.,2.],[2.,2.]]]}
    point=nav.next_target([-2.,0.,0.],[10.,0.,0.])
    assert nav.scene_signature!=signature
    assert nav.obstacles.clear((-2.,0.),(point[0],point[2]))
    assert not nav.obstacles.clear((-2.,0.),(10.,0.))


def test_unconfirmed_oak_does_not_suppress_fluorite_at_same_location():
    s=scene();s['objects']=[target()];game=Game();attempts=[];events=[]
    def runner(d,size,rf,device,window,controls,report,**kw):
        p=d['points'][0];r=rf()
        try:
            if 'special_resource' in p:
                k=p['special_resource']['category'];attempts.append(k)
                report('collecting','result',resource_outcome='unknown' if k=='roaming_oak' else 'gathered',resource_confirmed=k!='roaming_oak')
                if k=='roaming_oak':s['objects'].append(target(category='fluorite',resource_static_id=42002))
            else:game.position=p['position'];r.read()
            return True
        finally:r.close();device.release_all()
    nav=LocalNavigator(lambda:s,clock=game.monotonic,water_areas=())
    assert not drive_special_patrol(joint(s),game.size(),lambda:game,game,game,game,
        lambda st,tx,**kw:events.append(kw),clock=game,navigator=nav,point_runner=runner)
    assert attempts==['roaming_oak','fluorite']
    assert events[-1]['patrol_completed_resources']==['fluorite']
