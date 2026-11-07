"""Collection movement port: offline devices only, never uses native guidance."""
from copy import deepcopy
import math
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import Game, manual_points
from tests.unit.test_coordinate_navigation import scene, box
from utils.core.collection_navigation import CollectionNavigator
from utils.core.coordinate_navigation import ObstacleMap, NavigationWait
from utils.core import thresholds as policy
from utils.flows.coordinate_player import drive_points


BASIS={'forward':[0.,1.],'right':[1.,0.]}


def game_and_nav(source=None):
    game=Game()
    game.movement_basis=lambda:BASIS
    nav=CollectionNavigator(lambda:deepcopy(source or scene()),clock=game.monotonic,
                            scene_clock=game.monotonic,water_areas=())
    return game,nav


def run(game,nav,positions,report=None,**kwargs):
    return drive_points(manual_points(positions),game.size(),lambda:game,game,game,game,
                        report or Mock(),clock=game,navigator=nav,**kwargs)


def test_collections_do_not_require_a_guidance_reader_and_keep_order_wait_and_loops():
    game,nav=game_and_nav();reached=[]
    def report(state,message,**kw):
        if state=='reached':reached.append((kw['cycle'],kw['waypoint']))
    data=manual_points([(0,0,4),(3,0,4)]);before=deepcopy(data)
    assert drive_points(data,game.size(),lambda:game,game,game,game,report,clock=game,
                        navigator=nav,collect_wait_seconds=.2,loop_count=2)
    assert data==before and reached==[(1,1),(1,2),(2,1),(2,2)]
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==4
    assert not game.keys and game.closed


def test_final_approach_is_continuous_until_arrival_instead_of_pulsing():
    game,nav=game_and_nav()
    assert run(game,nav,[(0,0,5)])
    forward=[e for _,e in game.events if e=={'type':'key_down','key':'w'}]
    assert len(forward)==1
    assert math.dist(game.position,(0,0,5))<=policy.COORD_REACH_RADIUS


def test_identical_scene_refresh_does_not_create_input_releases():
    game,nav=game_and_nav();game.stop_at=.9
    assert not run(game,nav,[(0,0,10)])
    assert sum(e=={'type':'key_down','key':'w'} for _,e in game.events)==1
    assert not game.keys


def test_saved_collection_destination_is_never_replaced_by_shore_aim():
    game,nav=game_and_nav();target=(3.,0.,3.)
    nav.obstacles=ObstacleMap(scene(),(0,0,0),water_areas=())
    assert nav.respect_water is True and nav._shore_target((0,0,0),target,lambda:None) is None


def test_small_direction_noise_is_damped_but_turns_are_not_delayed():
    game,nav=game_and_nav();nav.obstacles=ObstacleMap(scene(),(0,0,0),water_areas=())
    for i,angle in enumerate([22.4,22.6]*8):
        game.time=i*.05;r=math.radians(angle)
        assert nav.steering_keys((0,0,0),(20*math.sin(r),0,20*math.cos(r)),BASIS,.6)=={'w'}
    game.time+=.05
    assert nav.steering_keys((0,0,0),(20,0,0),BASIS,.6)=={'d'}


@pytest.mark.parametrize('water',[True,False])
def test_collection_retains_water_and_actual_wall_constraints(water):
    game,nav=game_and_nav();wall=box(-4,4,1,2)
    if water:nav.water_areas=[wall['polygon']]
    else:nav.scene_provider=lambda:scene(wall)
    nav.next_target((0,0,0),(0,0,4))
    assert not nav.obstacles.clear((0,0),(0,4))


def test_missing_scene_never_keeps_the_last_movement_permit():
    game,nav=game_and_nav();game.stop_at=1.0
    nav.scene_provider=lambda:scene() if game.time<.4 else None
    assert not run(game,nav,[(0,0,10)])
    assert not game.keys
    assert all(e['type']!='key_down' for at,e in game.events if at>.45)


def test_worker_default_collection_navigator_and_custom_factory_stay_separate():
    from utils.ui.point_playback import PointPlayback
    from tests.unit.test_point_playback import FakeWindow,WaitingControls,points
    provider=Mock(return_value=scene());runner=Mock(return_value=True)
    worker=PointPlayback('profile',runner=runner,window_factory=FakeWindow,
        controls_factory=WaitingControls,device_factory=lambda w:Mock(),scene_provider=provider)
    worker.start(points(),77,auto_navigate=True);worker.thread.join(2)
    try:
        assert type(runner.call_args.kwargs['navigator']) is CollectionNavigator
        assert runner.call_args.kwargs['navigator'].scene_provider() is provider.return_value
    finally:worker.close()


def follower(game,nav):
    from utils.flows.collection_player import CollectionFollower
    f=CollectionFollower({'duration':0.,'client_size':game.size()},game,game,game,game,Mock(),game,
                         waypoints=[{'position':(0,0,10)}],navigator=nav)
    f.read(force=True);nav.next_target(game.position,(0,0,10))
    return f


@pytest.mark.parametrize('work,expected',[(0.,.05),(.02,.03),(.07,0.)])
def test_collection_cycle_subtracts_planning_work(work,expected):
    game,nav=game_and_nav();f=follower(game,nav);game.time=work
    f.wait_movement(0.,10.,False)
    assert f.cycle_metrics['cycle_sleep_requested']==pytest.approx(expected)
    assert game.time==pytest.approx(max(work,.05))


def test_late_position_cannot_press_or_extend_a_permit():
    from utils.flows.coordinate_player import _MovementRetry
    game,nav=game_and_nav();f=follower(game,nav)
    f.steering_travel(game.position,(0,0,10));game.time=.2
    with pytest.raises(_MovementRetry):f.apply_movement({'w'},game.position,(0,0,10))
    assert not game.keys and f.input_lease.deadline is None


def test_expired_cycle_yields_instead_of_busy_spinning_at_zero_time():
    game,nav=game_and_nav();f=follower(game,nav)
    f.steering_travel(game.position,(0,0,10));f.apply_movement({'w'},game.position,(0,0,10))
    game.time=.145;f.wait_movement(.145,10.,False)
    assert game.time>.145 and not game.keys


@pytest.mark.parametrize('interrupt',['stop','pause','focus','resize'])
def test_collection_watchdog_and_controller_resynchronize_after_release(interrupt):
    from utils.flows.coordinate_player import _MovementRetry
    game,nav=game_and_nav();f=follower(game,nav)
    f.steering_travel(game.position,(0,0,10));f.apply_movement({'w'},game.position,(0,0,10))
    if interrupt=='stop':game.stop_at=0.
    elif interrupt=='pause':game.running=False
    elif interrupt=='focus':game.focus=False
    else:game.size=lambda:[640,480]
    f.input_lease.poll()
    assert not game.keys
    with pytest.raises(_MovementRetry):f.guard()
    assert not f.keys and not f.input_lease.tripped


def test_collision_observation_deadline_is_not_replaced_by_publication_time():
    from tests.unit.test_navigation_supply import ready
    game,nav=game_and_nav();game.time=5.95
    source=ready(0.,5.9);nav.scene=source
    f=follower(game,nav);nav.scene=source
    f.steering_travel(game.position,(0,0,10))
    assert f.move_deadline==pytest.approx(6.)


def test_action_keys_revoke_normal_movement_lease_without_releasing_each_f_press():
    game,nav=game_and_nav();f=follower(game,nav)
    f.steering_travel(game.position,(0,0,10));f.apply_movement({'w'},game.position,(0,0,10))
    f.set_keys({'f'});f.set_keys({'f'})
    assert f.input_lease.deadline is None and game.keys=={'f'}
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==1
    f.release()


def test_no_path_uses_checked_retreat_then_rejoins_saved_destination():
    game,nav=game_and_nav();normal=nav.next_target;records=[]
    def blocked(position,target,*a,**kw):
        if not any(e=={'type':'key_down','key':'s'} for _,e in game.events):
            raise NavigationWait('no local connection')
        return normal(position,target,*a,**kw)
    nav.next_target=blocked
    assert run(game,nav,[(0,0,5)],lambda s,m,**kw:records.append(kw),
               continuous_recovery=True,recovery_attempts=5,jump_on_recovery=True)
    assert any(r.get('recovery_phase')=='path_escape' for r in records)
    assert not any(e['key']=='space' for _,e in game.events)
    assert math.dist(game.position,(0,0,5))<=policy.COORD_REACH_RADIUS


def test_no_safe_retreat_does_not_blindly_move_or_learn_a_wall():
    game,nav=game_and_nav(scene(box(-2,2,-2,2)));game.stop_at=7.
    nav.blocked=Mock();records=[]
    assert not run(game,nav,[(0,0,5)],lambda s,m,**kw:records.append(kw),
                   continuous_recovery=True,recovery_attempts=5,jump_on_recovery=True)
    assert any(r.get('recovery_result')=='no_safe_step' for r in records)
    assert not game.events;nav.blocked.assert_not_called()


def test_resource_objects_remain_available_outside_temporary_navigation_scope():
    game,nav=game_and_nav();original=scene();original['objects']=[{'kind':'resource','position':[1,0,1]}]
    nav.scene_provider=lambda:original
    nav.next_target((0,0,0),(0,0,5))
    assert nav.scene_provider() is original and len(nav.scene_provider()['objects'])==1


def walking_follower():
    game,nav=game_and_nav();f=follower(game,nav)
    f.steering_travel(game.position,(0,0,10))
    f.apply_movement({'w'},game.position,(0,0,10))
    return game,nav,f


def test_short_cycle_sleep_does_not_release_a_healthy_permit():
    game,nav,f=walking_follower();deadline=f.input_lease.deadline
    game.time=.045
    f.wait_movement(0.,10.,False)
    assert game.time==pytest.approx(.05) and game.keys=={'w'}
    assert f.input_lease.deadline==deadline
    assert not f.input_lease.metrics()['release_counts']


def test_wait_leaves_validation_time_without_extending_the_permit():
    game,nav,f=walking_follower();deadline=f.input_lease.deadline
    game.time=.12
    f.wait_movement(.10,10.,False)
    assert game.time==pytest.approx(.121) and game.keys=={'w'}
    assert f.input_lease.deadline==deadline
    f.read(force=True);f.steering_travel(game.position,(0,0,10))
    f.apply_movement({'w'},game.position,(0,0,10))
    assert f.input_lease.deadline==pytest.approx(.271)
    assert sum(e=={'type':'key_down','key':'w'} for _,e in game.events)==1


@pytest.mark.parametrize('reason',['map_build','path_search'])
def test_unchanged_planning_preserves_only_the_existing_checked_permit(reason):
    from utils.flows.coordinate_player import _MovementRetry
    game,nav,f=walking_follower();deadline=f.input_lease.deadline
    nav.release_reason=reason;game.time=.05
    f.planning_release()
    assert game.keys=={'w'} and f.input_lease.deadline==deadline
    # Even blocked computation cannot prolong movement. The same independent
    # watchdog used by the running program revokes the original permission.
    game.time=.151;f.input_lease.poll()
    assert not game.keys
    with pytest.raises(_MovementRetry):f.guard()


@pytest.mark.parametrize('change',['geometry','world','height','risk','reference','missing_map'])
@pytest.mark.parametrize('reason',['map_build','path_search'])
def test_changed_planning_context_still_releases_input(change,reason):
    game,nav,f=walking_follower();nav.release_reason=reason
    if change=='geometry':nav.scene_signature=('different',)
    elif change=='world':nav.scene={**nav.scene,'cache_context':'different'}
    elif change=='height':nav.obstacles.height+=1.
    elif change=='risk':nav.risks.revision+=1
    elif change=='reference':nav.reference_revision='different'
    else:nav.obstacles=None
    f.planning_release()
    assert not game.keys and f.input_lease.deadline is None


def test_slow_reads_are_attributed_without_renewing_movement():
    game,nav,f=walking_follower();deadline=f.input_lease.deadline
    original=game.read
    def read():
        assert f.input_lease.phase=='position_read'
        game.sleep(.03)
        return original()
    game.read=read;game.time=.06
    f.read(force=True)
    assert f.input_lease.deadline==deadline
