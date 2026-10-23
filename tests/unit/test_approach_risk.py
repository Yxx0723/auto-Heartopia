"""Directed temporary costs are not learned physical walls."""
from utils.core.approach_risk import ApproachRisks
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap, plan_path
from tests.unit.test_coordinate_navigation import scene, box, assert_clear


def store():
    risks = ApproachRisks(clock=lambda: 0.)
    risks.bind('scene-a')
    risks.record((0, 0, 0), (0, 0, 4), attempt_id='one')
    return risks


def test_failure_is_directed_and_separated_by_height_and_movement_mode():
    risks = store()
    assert risks.cost((0, 0), (0, 3), 0) > 0
    assert risks.cost((0, 3), (0, 0), 0) == 0
    assert risks.cost((0, 0), (0, 3), 4) == 0
    assert risks.cost((0, 0), (0, 3), 0, mode='jump') == 0


def test_repeated_same_attempt_does_not_inflate_cost_or_count():
    risks = store(); before = risks.cost((0, 0), (0, 3), 0)
    for _ in range(10):
        risks.record((0, 0, 0), (0, 0, 4), attempt_id='one')
    assert risks.count == 1 and risks.cost((0, 0), (0, 3), 0) == before


def test_reverse_success_does_not_clear_forward_risk_and_walk_success_is_mode_specific():
    risks = store()
    risks.success((0, 0, 3), (0, 0, 0))
    assert risks.count == 1
    risks.success((0, 0, 0), (0, 0, 3), mode='jump')
    assert risks.count == 1
    risks.success((0, 0, 0), (0, 0, 3))
    assert risks.count == 0


def test_expiry_scene_switch_and_storage_are_bounded():
    risks = store()
    risks.expire(100.)
    assert risks.count == 0
    risks.record((0, 0, 0), (0, 0, 4), attempt_id='two')
    risks.bind('scene-b')
    assert risks.count == 0
    for i in range(100):
        risks.record((i, 0, 0), (i, 0, 4), attempt_id=str(i))
    assert risks.count <= 32


def test_planner_changes_approach_without_hard_blocking_either_endpoint_or_reverse():
    risks = store()
    obstacles = ObstacleMap(scene(), (0, 0, 0), water_areas=(), risks=risks)
    assert obstacles.clear((0, 0), (0, 4))
    path = plan_path((0, 0, 0), (0, 0, 6), obstacles)
    assert any(abs(p[0]) > .5 for p in path)
    assert_clear(path, obstacles, (0, 0, 0))
    reverse = plan_path((0, 0, 6), (0, 0, 0), obstacles)
    assert all(abs(p[0]) < 1e-9 for p in reverse)


def test_known_wall_still_blocks_and_smoothing_does_not_cut_risky_entry():
    obstacles = ObstacleMap(scene(box(-.5, .5, 2, 3)), (0, 0, 0), water_areas=(), risks=store())
    path = plan_path((0, 0, 0), (0, 0, 6), obstacles)
    assert_clear(path, obstacles, (0, 0, 0))
    assert not obstacles.clear((0, 0), (0, 6))


def test_block_learning_never_generates_an_undirected_probe():
    nav = LocalNavigator(lambda: scene(), water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 6))
    nav.blocked((0, 0, 0), (0, 0, 3))
    assert nav.probes == [] and nav.risks.count == 1
    following = nav.next_target((0, 0, 0), (0, 0, 6))
    assert abs(following[0]) > .1


def test_scope_change_invalidates_old_risks_and_cached_path():
    state = [scene()]
    state[0]['cache_context'] = 'a'
    nav = LocalNavigator(lambda: state[0], water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 6));nav.blocked((0, 0, 0), (0, 0, 3))
    state[0] = {**scene(), 'cache_context': 'b'}
    following = nav.next_target((0, 0, 0), (0, 0, 6))
    assert nav.risks.count == 0 and following[0] == 0


def test_local_geometry_change_invalidates_risk_but_far_change_does_not():
    state = [scene()]
    nav = LocalNavigator(lambda: state[0], water_areas=())
    nav.next_target((0,0,0),(0,0,6));nav.blocked((0,0,0),(0,0,3))
    state[0] = scene(box(15,16,15,16))
    nav.next_target((0,0,0),(0,0,6))
    assert nav.risks.count == 1
    state[0] = scene(box(2,3,-1,1))
    nav.next_target((0,0,0),(0,0,6))
    assert nav.risks.count == 0


def test_risk_expiry_invalidates_old_detour_and_replans_straight():
    now = [0.]
    nav = LocalNavigator(lambda: scene(), clock=lambda: now[0], water_areas=())
    nav.next_target((0,0,0),(0,0,6));nav.blocked((0,0,0),(0,0,3))
    assert nav.next_target((0,0,0),(0,0,6))[0] != 0
    now[0] = 100.
    assert nav.next_target((0,0,0),(0,0,6))[0] == 0
