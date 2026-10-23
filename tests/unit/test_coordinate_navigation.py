"""Geometry and closed-loop navigation tests never touch the real game."""
import math

import pytest

from tests.unit.test_coordinate_player import Game, manual_points
from utils.core.coordinate_navigation import (LocalNavigator, NavigationWait, ObstacleMap,
                                              plan_path, polygon_distance)
from utils.core.coordinate_route import distance
from utils.flows.coordinate_player import drive_points


def box(left=-2., right=2., low=2., high=3., height=(-.5, 2.)):
    return {'polygon': [[left, low], [right, low], [right, high], [left, high]], 'height': list(height)}


def scene(*boxes):
    return {'state': 'ready', 'objects': [], 'colliders': list(boxes)}


def assert_clear(path, obstacles, start=(0., 0., 0.)):
    for point in path:
        assert obstacles.clear((start[0], start[2]), (point[0], point[2]), escape=True)
        start = point


def test_astar_routes_around_wall_and_all_shortcuts_have_clearance():
    obstacles = ObstacleMap(scene(box()), (0, 0, 0), water_areas=())
    path = plan_path((0, 0, 0), (0, 0, 8), obstacles)
    assert max(abs(point[0]) for point in path) > 2.6
    assert path[-1] == (0., 0, 8.)
    assert_clear(path, obstacles)


def test_thin_obstacle_cannot_be_skipped_between_grid_centers():
    obstacles = ObstacleMap(scene(box(low=2.17, high=2.18)), (0, 0, 0), water_areas=())
    path = plan_path((0, 0, 0), (0, 0, 7), obstacles)
    assert max(abs(point[0]) for point in path) > 2.6
    assert_clear(path, obstacles)


def test_ground_and_overhead_boxes_do_not_block_free_floor():
    obstacles = ObstacleMap(scene(box(-100, 100, -100, 100, (-2, 0)),
                                  box(-100, 100, -100, 100, (3, 4))), (0, 0, 0), water_areas=())
    path = plan_path((0, 0, 0), (0, 0, 8), obstacles)
    assert all(p[0] == 0 for p in path)
    assert path[-1] == (0., 0, 8.)


def test_water_outline_is_avoided_without_a_collider():
    water = [box()['polygon']]
    obstacles = ObstacleMap(scene(), (0, 0, 0), water_areas=water)
    path = plan_path((0, 0, 0), (0, 0, 8), obstacles)
    assert max(abs(p[0]) for p in path) > 3.
    assert_clear(path, obstacles)


def test_start_inside_padding_can_escape_but_never_cross_actual_wall():
    obstacles = ObstacleMap(scene(box(left=0, right=1, low=-5, high=5)), (-.3, 0, 0), water_areas=())
    assert obstacles.clear((-.3, 0), (-1., 0), escape=True)
    assert not obstacles.clear((-.3, 0), (2., 0), escape=True)
    assert not obstacles.clear((.5, 0), (-1., 0), escape=True)
    assert_clear(plan_path((-.3, 0, 0), (-5, 0, 0), obstacles), obstacles, (-.3, 0, 0))


def test_inaccessible_target_waits_instead_of_returning_a_straight_line():
    obstacles = ObstacleMap(scene(box(-2, 2, 6, 9)), (0, 0, 0), water_areas=())
    with pytest.raises(NavigationWait):
        plan_path((0, 0, 0), (0, 0, 8), obstacles)


def test_recorded_collection_point_near_a_trunk_is_reachable_without_crossing_the_trunk():
    trunk = box(-.25, .25, 7.7, 8.2)
    obstacles = ObstacleMap(scene(trunk), (0, 0, 0), water_areas=())
    target = (0, 0, 8.35)  # Player stood 0.15 outside the solid trunk, inside the old 0.6 padding.
    path = plan_path((0, 0, 0), target, obstacles)
    assert path[-1] == target and max(abs(p[0]) for p in path) > .3
    previous = (0, 0, 0)
    for point in path:
        assert polygon_distance((previous[0], previous[2]), (point[0], point[2]), trunk['polygon']) > .05
        previous = point
    assert not obstacles.clear((-.4, 6.8), (-.4, 7.6))  # Transit padding remains intact.


def test_recorded_endpoint_never_relaxes_water_clearance():
    water = box(-.25, .25, 7.7, 8.2)['polygon']
    obstacles = ObstacleMap(scene(), (0, 0, 0), water_areas=[water], goal=(0, 0, 8.35))
    assert not obstacles.terminal_padding
    assert not obstacles.clear((0, 8.35), (0, 8.35))


def test_confirmed_same_direction_motion_can_clear_risk_without_creating_a_probe_wall():
    snapshot = scene()
    nav = LocalNavigator(lambda: snapshot, water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 8))
    nav.blocked((0, 0, 0), (0, 0, 3))
    assert not nav.probes and nav.risks.count == 1
    nav.motion_succeeded((0, 0, 0), (0, 0, 3))
    following = nav.next_target((0, 0, 3), (0, 0, 8))
    assert not nav.risks.count and following[2] > 3


def test_passing_temporary_legs_does_not_turn_back_and_final_arrival_remains_precise():
    snapshot = scene()
    nav = LocalNavigator(lambda: snapshot, water_areas=())
    target = (0, 0, 12)
    assert nav.next_target((0, 0, 0), target)[2] == 3
    assert nav.next_target((0, 0, 7.5), target)[2] == 9
    assert nav.next_target((0, 0, 11.5), target) == target


def test_far_target_has_a_bounded_local_plan_and_keeps_the_original_goal():
    nav = LocalNavigator(lambda: scene(), water_areas=())
    target = (0, 0, 200)
    nav.next_target((0, 0, 0), target)
    assert nav.goal == target and nav.path[-1][2] == 24.
    nav.next_target((0, 0, 24), target)
    assert nav.goal == target and nav.path[-1][2] == 48.


def test_next_horizon_indexes_obstacles_outside_previous_local_window():
    snapshot = scene(box(low=40, high=41))
    nav = LocalNavigator(lambda: snapshot, water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 200))
    assert all(p[0] == 0 for p in nav.path)
    nav.next_target((0, 0, 24), (0, 0, 200))
    assert max(abs(p[0]) for p in nav.path) > 2.6
    assert_clear(nav.path, nav.obstacles, (0, 0, 24))


def test_search_checks_for_interruptions_and_does_not_swallow_them():
    checks = []
    def check():
        checks.append(True)
        if len(checks) == 2:
            raise RuntimeError('cancelled')
    obstacles = ObstacleMap(scene(box()), (0, 0, 0), water_areas=())
    with pytest.raises(RuntimeError, match='cancelled'):
        plan_path((0, 0, 0), (0, 0, 8), obstacles, check)


def test_new_scene_obstacle_invalidates_cached_plan():
    current = [scene()]
    nav = LocalNavigator(lambda: current[0], water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert all(p[0] == 0 for p in nav.path)
    current[0] = scene(box())
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert max(abs(p[0]) for p in nav.path) > 2.6
    current[0] = None
    with pytest.raises(NavigationWait, match='场景'):
        nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.path == []


class NavigationGame(Game):
    def __init__(self, angle=0, physical=()):
        super().__init__(angle=angle)
        self.physical = physical
        self.collisions = 0

    def sleep(self, dt):
        before = self.position.copy()
        super().sleep(dt)
        a, b = (before[0], before[2]), (self.position[0], self.position[2])
        if a != b and any(polygon_distance(a, b, polygon) <= .05 for polygon in self.physical):
            self.position[:] = before
            self.collisions += 1


def navigate(game, data, provider, report=lambda *a, **kw: None, **options):
    nav = LocalNavigator(provider, clock=game.monotonic, water_areas=())
    return drive_points(data, game.size(), lambda: game, game, game, game, report,
                        clock=game, navigator=nav, **options)


@pytest.mark.parametrize('angle', [0, math.pi / 3, -math.pi / 2])
def test_feedback_navigation_avoids_wall_with_rotated_camera_and_collects_only_saved_points(angle):
    obstacle = box()
    game = NavigationGame(angle, [obstacle['polygon']])
    targets = [(0, 0, 8), (4, 0, 8)]
    reached, collected, paths = [], [], []
    def report(state, message, **extra):
        if state == 'reached':
            reached.append(extra['waypoint'])
        if state == 'collecting':
            collected.append((extra['waypoint'], game.read()))
        if extra.get('navigation_path'):
            paths.append(extra['navigation_path'])
    assert navigate(game, manual_points(targets), lambda: scene(obstacle), report,
                    collect_wait_seconds=.2, loop_count=2)
    assert reached == [1, 2, 1, 2]
    assert len(collected) == 4 and paths
    assert all(distance(position, targets[index - 1]) <= .3 for index, position in collected)
    assert sum(event == {'type': 'key_down', 'key': 'f'} for _, event in game.events) == 4
    assert game.collisions == 0 and not game.keys and game.closed


def test_far_target_is_reached_by_replanning_multiple_local_horizons():
    game = Game(speed=3)
    assert navigate(game, manual_points([(0, 0, 100)]), lambda: scene())
    assert distance(game.read(), (0, 0, 100)) <= .3 and not game.keys


def test_u_shaped_obstacle_allows_initial_movement_away_from_saved_target():
    boxes = [box(-3, 3, 2, 3), box(-3, -2, -2, 3), box(2, 3, -2, 3)]
    game = NavigationGame(physical=[b['polygon'] for b in boxes])
    snapshot = scene(*boxes)
    phases, positions = [], []
    def report(state, message, **extra):
        phases.append(state)
        positions.append(game.read())
    assert navigate(game, manual_points([(0, 0, 8)]), lambda: snapshot, report)
    assert min(p[2] for p in positions) < -2.6
    assert 'recovering' not in phases and game.collisions == 0
    assert not game.keys


def test_waiting_for_scene_never_sends_movement_or_collection_then_resumes():
    game = Game()
    current = scene(box())
    assert navigate(game, manual_points([(0, 0, 8)]), lambda: current if game.time >= 4. else None,
                    collect_wait_seconds=0.)
    assert not [event for at, event in game.events if 2.1 < at < 4. and event['type'] == 'key_down']
    assert not game.keys and game.closed


def test_navigation_wait_does_not_consume_movement_time_budget(monkeypatch):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_POINT_PLAYBACK_SECONDS', 6.)
    game = Game()
    current = scene()
    assert navigate(game, manual_points([(0, 0, 8)]), lambda: current if game.time >= 10. else None)
    assert game.time > 10. and not game.keys


@pytest.mark.parametrize('interrupt', ['stop', 'invalid', 'focus'])
def test_no_route_wait_is_interruptible_and_releases_all_input(interrupt):
    game = Game()
    game.stop_at = 4.
    if interrupt == 'invalid':
        game.invalid_at = 3.
    if interrupt == 'focus':
        sleep = game.sleep
        def lose_focus(dt):
            sleep(dt)
            if game.time >= 3.:
                game.focus = False
        game.sleep = lose_focus
    data = manual_points([(0, 0, 8)])
    if interrupt == 'invalid':
        with pytest.raises(ValueError, match='lost position'):
            navigate(game, data, lambda: None)
    else:
        assert not navigate(game, data, lambda: None)
    assert not any(event['key'] == 'f' for _, event in game.events)
    assert not game.keys and game.closed


def test_unmapped_obstacle_uses_recovery_and_replans_without_skipping_target():
    game = NavigationGame(physical=[box(left=-.8, right=.8)['polygon']])
    reached, retries = [], []
    def report(state, message, **extra):
        if state == 'reached':
            reached.append(extra['waypoint'])
        if state == 'navigating':
            retries.append(message)
    assert navigate(game, manual_points([(0, 0, 8)]), lambda: scene(), report,
                    recovery_attempts=2, continuous_recovery=True, collect_wait_seconds=0.)
    assert reached == [1] and game.collisions > 0
    assert not game.keys and game.closed


def test_calibration_beside_a_wall_uses_the_unblocked_reverse_axis_and_continues():
    obstacle = box(-2., 2., .35, .55)
    game = NavigationGame(physical=[obstacle['polygon']])
    collected = []
    def report(state, message, **extra):
        if state == 'collecting':
            collected.append(extra['waypoint'])
    assert navigate(game, manual_points([(0, 0, 5)]), lambda: scene(obstacle), report,
                    recovery_attempts=2, continuous_recovery=True, collect_wait_seconds=0.)
    assert collected == [1] and distance(game.read(), (0, 0, 5)) <= .3
    assert not game.keys


def test_camera_rotation_recalibrates_and_reaches_the_same_saved_point():
    game = Game()
    game.rotate_at = 2.5
    recalibrations, reached = [], []
    def report(state, message, **extra):
        if state == 'calibrating':
            recalibrations.append(game.time)
        if state == 'reached':
            reached.append(extra['waypoint'])
    assert navigate(game, manual_points([(0, 0, 12)]), lambda: scene(), report,
                    recovery_attempts=2, continuous_recovery=True, collect_wait_seconds=0.)
    assert len(recalibrations) >= 2 and reached == [1]
    assert distance(game.read(), (0, 0, 12)) <= .3 and not game.keys


def test_fresh_no_path_can_try_recovery_but_missing_scene_never_sends_a_jump():
    game = Game()
    current = [scene(box(-1, 1, 6, 9))]
    retries = []
    def report(state, message, **extra):
        if extra.get('recovery_phase') == 'jump':
            retries.append(extra['waypoint'])
            current[0] = scene()
    assert navigate(game, manual_points([(0, 0, 8)]), lambda: current[0], report,
                    recovery_attempts=2, continuous_recovery=True, jump_on_recovery=True, collect_wait_seconds=0.)
    assert retries == [1] and distance(game.read(), (0, 0, 8)) <= .3
    assert not game.keys
    game = Game()
    game.stop_at = 8.
    assert not navigate(game, manual_points([(0, 0, 8)]), lambda: None,
                        recovery_attempts=2, continuous_recovery=True, jump_on_recovery=True)
    assert not any(event['key'] == 'space' for _, event in game.events)


def test_transient_jump_height_at_a_point_waits_for_landing_before_collection():
    game = Game()
    original_read = game.read
    landed = [None]
    def jumping_read():
        p = original_read()
        if distance(p, (0, 0, 5)) <= .3 and landed[0] is None:
            landed[0] = game.time + 1.2
        height = 0.
        if landed[0] is not None:
            elapsed = game.time - (landed[0] - 1.2)
            height = 1.5 * max(0., min(1., elapsed / .2, (1.2 - elapsed) / .2))
        return (p[0], height, p[2])
    game.read = jumping_read
    collected = []
    def report(state, message, **extra):
        if state == 'collecting':
            collected.append(game.time)
    assert navigate(game, manual_points([(0, 0, 5)]), lambda: scene(), report,
                    recovery_attempts=2, continuous_recovery=True, collect_wait_seconds=0.)
    assert collected and collected[0] >= landed[0]
    assert not game.keys


def test_blocked_calibration_retries_without_collection_and_manual_stop_still_works():
    game = Game(speed=0)
    game.stop_at = 3.2
    phases = []
    assert not navigate(game, manual_points([(0, 0, 5)]), lambda: scene(),
                        lambda state, message, **extra: phases.append(state),
                        recovery_attempts=2, continuous_recovery=True, jump_on_recovery=True)
    assert phases.count('calibrating') >= 2
    assert any(event['key'] == 'space' for _, event in game.events)
    assert not any(event['key'] == 'f' for _, event in game.events)
    assert not game.keys and game.closed


def test_blocked_calibration_does_not_jump_while_scene_data_is_unavailable():
    game = Game(speed=0)
    game.stop_at = 3.2
    assert not navigate(game, manual_points([(0, 0, 5)]), lambda: None,
                        recovery_attempts=2, continuous_recovery=True, jump_on_recovery=True)
    assert not any(event['key'] == 'space' for _, event in game.events)
    assert not game.keys and game.closed


def test_pause_during_a_calibration_retry_releases_keys_then_resumes_the_same_point():
    game = Game(speed=0)
    phases, reached = [], []
    resume_at = [math.inf]
    sleep = game.sleep
    def resume_movement(dt):
        game.speed = 3 if game.time >= resume_at[0] else 0
        sleep(dt)
    game.sleep = resume_movement
    def report(state, message, **extra):
        if state == 'calibrating':
            phases.append(game.time)
            if len(phases) == 2:
                game.pause_at, game.resume_at = game.time + .1, game.time + 1.
                resume_at[0] = game.resume_at
        if state == 'reached':
            reached.append(extra['waypoint'])
    assert navigate(game, manual_points([(0, 0, 5)]), lambda: scene(), report,
                    recovery_attempts=2, continuous_recovery=True, jump_on_recovery=True)
    assert reached == [1]
    assert not [event for at, event in game.events if game.pause_at + .05 < at < game.resume_at
                and event['type'] == 'key_down']
    assert not game.keys and game.closed
