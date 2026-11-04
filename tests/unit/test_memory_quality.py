"""Execution memory must not replay exact recovery loops or pin an inferior shape."""
from utils.drivers.route_path_memory import RoutePathMemory
from tests.unit.test_coordinate_player import manual_points


def test_timed_success_removes_exact_loop_without_inventing_faster_duration(tmp_path):
    memory=RoutePathMemory(tmp_path/'route.json',manual_points([(0,0,5)]))
    path=[[0,0,0],[0,0,1],[1,0,1],[1,0,2],[0,0,1],[0,0,2],[0,0,3],[0,0,4],[0,0,5]]
    assert memory.remember(0,path,8.,sample_times=list(range(9)))
    stored=memory.legs['0'][0]
    assert stored==[[0,0,i] for i in range(6)]
    record=memory.statistics[memory._identity(stored)]
    assert record['sample_times'][0]==0 and record['sample_times'][-1]==8
    assert record['profile_duration']==8


def test_existing_legacy_loop_is_pruned_only_in_execution_view(tmp_path):
    memory=RoutePathMemory(tmp_path/'route.json',manual_points([(0,0,5)]))
    path=[[0,0,0],[0,0,1],[1,0,1],[1,0,2],[0,0,1],[0,0,2],[0,0,3],[0,0,4],[0,0,5]]
    memory.legs={'0':[path]}
    result=memory.lookup(0,(0,0,0))
    assert result==[(0,0,i) for i in range(6)]
    assert memory.legs['0'][0]==path


def test_new_shorter_success_is_not_replaced_with_old_similar_detour(tmp_path):
    memory=RoutePathMemory(tmp_path/'route.json',manual_points([(0,0,5)]))
    old=[[0,0,0],[.49,0,1],[-.49,0,2],[.49,0,3],[-.49,0,4],[0,0,5]]
    new=[[0,0,i] for i in range(6)]
    assert memory.remember(0,old,8.)
    assert memory.remember(0,new,3.)
    assert new in memory.legs['0']


def ready_scene(*colliders):
    from tests.unit.test_coordinate_navigation import scene
    result=scene(*colliders)
    result['cache_context']='world'
    result['quality']={'navigation_eligible':True,'root_verified_at':0.,'parts':{
        k:{'state':'complete','observed_from':0.,'observed_to':0.}
        for k in ('managed_collisions','native_collisions')}}
    return result


def navigator(tmp_path,source):
    from utils.core.collection_navigation import CollectionNavigator
    memory=RoutePathMemory(tmp_path/'route.json',manual_points([(0,0,8)]))
    assert memory.remember(0,[[0,0,0],[-4,0,0],[-4,0,4],[-4,0,8],[0,0,8]],10.)
    nav=CollectionNavigator(lambda:source,clock=lambda:0.,scene_clock=lambda:0.,water_areas=())
    nav.path_memory=memory;nav.set_waypoint(0)
    return nav,memory


def test_verified_shorter_trial_does_not_overwrite_success_memory(tmp_path):
    from copy import deepcopy
    nav,memory=navigator(tmp_path,ready_scene());before=deepcopy(memory.legs)
    assert nav.next_target((0,0,0),(0,0,8))==(0,0,8)
    assert nav.last_decision['kind']=='checked_shortcut_trial'
    assert memory.legs==before


def test_actual_wall_still_prevents_shortcut(tmp_path):
    from tests.unit.test_coordinate_navigation import box
    nav,_=navigator(tmp_path,ready_scene(box()))
    aim=nav.next_target((0,0,0),(0,0,8))
    assert aim!=(0,0,8) and nav.last_decision.get('kind')!='checked_shortcut_trial'


def test_failed_shortcut_returns_to_old_corridor_without_penalizing_it(tmp_path):
    nav,memory=navigator(tmp_path,ready_scene())
    nav.next_target((0,0,0),(0,0,8));identity=memory._identity(memory.legs['0'][0])
    nav.blocked((0,0,0),(0,0,4),attempt_id=1)
    nav.next_target((0,0,0),(0,0,8))
    assert nav.last_decision.get('kind')!='checked_shortcut_trial'
    assert identity not in memory.rejected


def test_fresh_jitter_corridor_uses_forward_lookahead_instead_of_tiny_targets(tmp_path):
    from utils.core.coordinate_navigation import ObstacleMap
    nav,_=navigator(tmp_path,ready_scene());nav.scene=ready_scene()
    nav.obstacles=ObstacleMap(nav.scene,(0,0,0),water_areas=())
    nav.memory_path=[((.2 if i%2 else -.2),0,i*.4) for i in range(1,7)]+[(0,0,3)]
    assert nav._memory_target((0,0,0))==(0,0,3)


def test_passed_sample_does_not_require_walking_back_to_hit_exact_point(tmp_path):
    from utils.core.coordinate_navigation import ObstacleMap
    nav,_=navigator(tmp_path,ready_scene());nav.scene=ready_scene()
    nav.obstacles=ObstacleMap(nav.scene,(0,0,1.2),water_areas=())
    nav.memory_path=[(0,0,1),(0,0,2),(0,0,3)];nav.memory_aim=(0,0,1)
    nav._advance_memory_progress((.2,0,1.2))
    assert nav.memory_path==[(0,0,2),(0,0,3)] and nav.memory_aim is None


def test_nearby_return_is_not_spliced_into_an_unobserved_connection(tmp_path):
    memory=RoutePathMemory(tmp_path/'route.json',manual_points([(0,0,5)]))
    path=[[0,0,0],[0,0,1],[1,0,1],[1,0,2],[.1,0,1],[0,0,2],[0,0,3],[0,0,4],[0,0,5]]
    assert memory.remember(0,path,8.,sample_times=list(range(9)))
    assert memory.legs['0'][0]==path


def test_stale_collision_or_missing_quality_never_authorizes_a_shortcut(tmp_path):
    source=ready_scene();nav,_=navigator(tmp_path,source)
    nav.scene_clock=lambda:100.
    nav.next_target((0,0,0),(0,0,8))
    assert nav.short_trial is None and nav.collision_deadline(0.) is None
    source.pop('quality');nav.scene_clock=lambda:0.
    nav.short_check_at=float('-inf')
    nav.next_target((0,0,0),(0,0,8))
    assert nav.short_trial is None


def test_shortcut_never_joins_another_height_layer(tmp_path):
    nav,_=navigator(tmp_path,ready_scene())
    nav.next_target((0,0,0),(0,.8,8))
    assert nav.short_trial is None


def test_unknown_scene_scope_does_not_authorize_a_shortcut(tmp_path):
    source=ready_scene();source.pop('cache_context')
    nav,_=navigator(tmp_path,source)
    nav.next_target((0,0,0),(0,0,8))
    assert nav.short_trial is None
