"""Definition-centred mineral navigation and physical-progress regressions."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import Game, manual_points
from utils.core import thresholds as policy
from utils.core.coordinate_points import ordered_waypoints
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap
from utils.core.mineral_approach import mineral_identity, mineral_standpoint
from utils.core.visit_progress import MotionProgress
from utils.flows.coordinate_player import _Follower, drive_points

CENTER = (-131.38699340820312, 19.089000701904297, -70.01100158691406)


def data(center=CENTER):
    route = manual_points([center])
    route['points'][0]['source'] = {'kind':'ore', 'item_type':302, 'resource_id':436,
                                   'source':'main-map-resource-definition', 'stand_position':'resource-center'}
    return route


def scene(center=CENTER, **changes):
    return {'state':'ready', 'world_state':'ready', 'colliders':[],
            'objects':[{'category':'ore', 'kind':'entity', 'map_static':True, 'position':list(center)}], **changes}


def play(game, route, provider, report=None, **options):
    nav = LocalNavigator(provider, clock=game.monotonic, water_areas=())
    return drive_points(route, game.size(), lambda:game, game, game, game, report or Mock(),
                        clock=game, navigator=nav, collect_wait_seconds=0., recovery_attempts=5,
                        continuous_recovery=True, stable_navigation=True, **options)


@pytest.mark.parametrize('position', [
    (-131.0006103515625,19.167943954467773,-70.08477020263672),
    (-131.6090087890625,19.127378463745117,-70.39276123046875),
    (-131.7149658203125,19.493907928466797,-69.90995788574219),
])
def test_actual_three_stalls_beside_ore_can_collect_without_walking_into_its_center(position):
    game, report = Game(speed=0), Mock()
    game.position=list(position);game.stop_at=8.
    route=data();before=deepcopy(route)
    assert play(game, route, scene, report)
    assert not any(c.args[0] in ('calibrating','recovering') for c in report.call_args_list)
    assert sum(e == {'type':'key_down','key':'f'} for _,e in game.events) == 1
    assert not any(e.get('key') in ('w','a','s','d','space') for _,e in game.events)
    assert route==before and not game.keys


def test_manual_point_at_same_coordinates_keeps_strict_arrival_rule():
    game, report = Game(speed=0), Mock()
    game.position=[CENTER[0]+.4,CENTER[1],CENTER[2]];game.stop_at=3.
    assert not play(game, manual_points([CENTER]), scene, report)
    assert not any(e.get('key')=='f' for _,e in game.events)


def test_nearest_start_is_chosen_before_any_resource_point_is_reanchored():
    game = Game(speed=0);game.position=list(CENTER)
    route = manual_points([(CENTER[0]+20,CENTER[1],CENTER[2]), CENTER])
    route['points'][1]['source']=data()['points'][0]['source']
    report=Mock()
    assert play(game, route, scene, report, start_mode='nearest')
    assert [c.kwargs['waypoint'] for c in report.call_args_list if c.args[0]=='reached']==[2]
    assert sum(e == {'type':'key_down','key':'f'} for _,e in game.events)==1


@pytest.mark.parametrize('fault', ['cached','definition','missing','ambiguous','wrong_category'])
def test_missing_or_reference_only_objects_never_authorize_f(fault):
    snap=scene()
    if fault=='cached':snap['objects'][0]['map_cached']=True
    if fault=='definition':snap['objects'][0]['map_definition']=True
    if fault=='missing':snap['objects']=[]
    if fault=='ambiguous':snap['objects']*=2
    if fault=='wrong_category':snap['objects'][0]['category']='rock'
    game,report=Game(speed=0),Mock();game.position=list(CENTER);game.stop_at=20.
    assert play(game,data(),lambda:snap,report,skip_unreachable=True)
    assert not any(e.get('key')=='f' for _,e in game.events)
    assert any(c.args[0]=='skipped' for c in report.call_args_list)
    assert not game.keys


def test_mineral_on_another_floor_never_uses_the_horizontal_proximity_rule():
    game=Game(speed=0);game.position=[CENTER[0],CENTER[1]+3,CENTER[2]];game.stop_at=3.
    assert not play(game,data(),scene)
    assert not any(e.get('key')=='f' for _,e in game.events)


def test_mineral_metadata_is_copied_and_unknown_sources_do_not_enable_approach():
    route=data();points=ordered_waypoints(route)
    assert mineral_identity(points[0])['resource_id']==436
    points[0]['source']['item_type']=301
    assert mineral_identity(points[0]) is None
    assert route['points'][0]['source']['item_type']==302
    assert mineral_identity(manual_points([CENTER])['points'][0]) is None


def test_ring_standpoints_cannot_open_water():
    center=(0.,0.,0.)
    water=(( -2.,-2.),(2.,-2.),(2.,2.),(-2.,2.))
    obstacles=ObstacleMap(scene(center),(-3,0,0),water_areas=(water,))
    assert mineral_standpoint((-3,0,0),center,obstacles) is None


def test_resource_disappearing_during_tool_check_is_rechecked_before_f():
    game=Game();game.position=list(CENTER)
    snap=scene();nav=LocalNavigator(lambda:snap,clock=game.monotonic,water_areas=())
    point=ordered_waypoints(data())[0]
    follower=_Follower({'duration':0.,'client_size':list(game.size())},game,game,game,game,Mock(),game,
                       waypoints=[point],navigator=nav,collect_wait_seconds=0.,tool_selector=object())
    follower.read_tool=lambda: None
    def lose_resource(*args):
        snap['objects']=[]
        return False
    follower.maintain_tool=lose_resource
    assert follower.prepare_mineral_point(point,0,1)
    assert not follower.collect_point(point,0,1)
    assert not any(e.get('key')=='f' for _,e in game.events)


def test_loops_reuse_original_resource_center_without_changing_saved_data():
    game=Game(speed=0);game.position=[CENTER[0]+.4,CENTER[1],CENTER[2]]
    route=data();before=deepcopy(route)
    assert play(game,route,scene,loop_count=2)
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==2
    assert route==before and not game.keys


def test_real_motion_watch_survives_centimetre_jitter_and_resets_on_actual_progress():
    watch=MotionProgress()
    assert not watch.stalled((0,0,0),0.)
    assert not watch.stalled((.05,0,.03),1.)
    assert watch.stalled((-.05,0,-.03),policy.COORD_STUCK_SECONDS)
    assert not watch.stalled((1.,0,0),policy.COORD_STUCK_SECONDS+.1)
    watch.reset()
    assert not watch.stalled((1.,0,0),100.)


def test_switching_temporary_legs_cannot_hide_a_stationary_character():
    game=Game();game.stop_at=8.
    nav=LocalNavigator(lambda:scene((0,0,20)),clock=game.monotonic,water_areas=())
    def next_target(position,target,**kwargs):
        return (position[0]+(1 if int(game.time*20)%2 else -1),position[1],position[2]+1)
    nav.next_target=next_target
    recovery=[]
    def status(state,message,**extra):
        if state=='playing' and '方向校准完成' in message:
            game.speed=0
        if extra.get('recovery_phase')=='start':
            recovery.append(game.time);game.stop_at=game.time
    assert not drive_points(manual_points([(0.,0.,20.)]),game.size(),lambda:game,game,game,game,status,
                            clock=game,navigator=nav,recovery_attempts=5,continuous_recovery=True)
    assert recovery and recovery[0]<6. and not game.keys


def test_a_new_block_discards_the_old_wide_guide_but_keeps_obstruction_probes():
    nav=LocalNavigator(scene,water_areas=())
    nav.guide_path=[(1,0,0),(2,0,0)];nav.guide_goal=(10,0,0);nav.guide_retry_at=100.
    nav.blocked((0,0,0),(10,0,0))
    assert nav.probes and not nav.guide_path and nav.guide_goal is None and nav.guide_retry_at==0.
