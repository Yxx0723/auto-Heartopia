"""Path quality, fresh-geometry reuse and bounded adaptive searches."""
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_navigation import NavigationGame, box, scene, assert_clear
from tests.unit.test_coordinate_player import Game, manual_points
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap, NavigationWait
from utils.core.coordinate_route import distance
from utils.drivers.route_path_memory import RoutePathMemory
from utils.flows.coordinate_player import _Follower, drive_points


def test_refreshing_identical_geometry_does_not_release_keys_or_rebuild_the_map():
    boxes = [box(-2, 2, 2, 3), box(-10, -9, 4, 5)]
    current = [scene(*boxes)]
    nav = LocalNavigator(lambda: current[0], water_areas=())
    release = Mock()
    nav.next_target((0, 0, 0), (0, 0, 12), before_plan=release)
    original = nav.obstacles
    for index in range(8):
        current[0] = scene(*reversed(boxes))
        current[0]['sequence'] = index
        nav.next_target((0, 0, .1), (0, 0, 12), before_plan=release)
    assert nav.obstacles is original and nav.stats == {'map_builds': 1, 'plans': 1}
    release.assert_called_once()


def test_only_covered_space_can_be_used_and_recentring_keeps_distant_obstacles():
    nav = LocalNavigator(lambda: scene(box(-2, 2, 40, 41)), water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 200))
    assert not nav.obstacles.clear((40, 0), (41, 0))
    nav.next_target((0, 0, 24), (0, 0, 200))
    assert max(abs(p[0]) for p in nav.path) > 2.6
    assert_clear(nav.path, nav.obstacles, (0, 0, 24))


def test_finer_search_finds_a_valid_gap_that_the_normal_grid_cannot_sample():
    walls = [box(-100, .3, 2, 4), box(2., 100, 2, 4)]
    nav = LocalNavigator(lambda: scene(*walls), water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.search_radius == 16. and nav.stats['plans'] == 2
    assert any(.9 < p[0] < 1.4 for p in nav.path)
    assert_clear(nav.path, nav.obstacles)


def test_wide_search_escapes_a_dead_end_outside_the_original_search_window():
    walls = [box(-42, 42, 4, 5), box(-42, -41, -42, 5), box(41, 42, -42, 5)]
    nav = LocalNavigator(lambda: scene(*walls), water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.search_radius == 60. and nav.stats['plans'] == 3
    assert min(p[2] for p in nav.path) < -42.6
    assert max(abs(p[0]) for p in nav.path) > 42.6
    assert nav.path[-1] == (0., 0, 8.)
    assert_clear(nav.path, nav.obstacles)


def test_scene_update_preserves_the_wide_detour_instead_of_reentering_the_dead_end():
    walls = [box(-42, 42, 4, 5), box(-42, -41, -42, 5), box(41, 42, -42, 5)]
    current = [scene(*walls)]
    nav = LocalNavigator(lambda: current[0], water_areas=())
    target = (0, 0, 8)
    nav.next_target((0, 0, 0), target)
    position = next(p for p in nav.path if p[2] < -30.)
    current[0] = scene(*walls, box(100, 101, 100, 101))
    nav.next_target(position, target)
    assert nav.obstacles.search_radius == 60.
    assert_clear(nav.path, nav.obstacles, position)


def test_expanded_search_is_interruptible_and_preserves_the_stop_exception():
    walls = [box(-42, 42, 4, 5), box(-42, -41, -42, 5), box(41, 42, -42, 5)]
    nav = LocalNavigator(lambda: scene(*walls), water_areas=())
    def stop():
        if nav.stats['plans'] >= 3:
            raise RuntimeError('manual stop')
    with pytest.raises(RuntimeError, match='manual stop'):
        nav.next_target((0, 0, 0), (0, 0, 8), check=stop)


def test_dense_remembered_straight_walk_has_bounded_planning_and_key_churn(tmp_path):
    game = Game()
    data = manual_points([(0, 0, 120)])
    memory = RoutePathMemory(tmp_path/'route.json', data)
    assert memory.remember(0, [[0., 0., z*.5] for z in range(241)])
    snapshots = {}
    def provider():
        return snapshots.setdefault(int(game.time / 2), scene())
    nav = LocalNavigator(provider, clock=game.monotonic, water_areas=(), path_memory=memory)
    assert drive_points(data, game.size(), lambda: game, game, game, game,
                        lambda *a, **kw: None, clock=game, navigator=nav, collect_wait_seconds=0.)
    assert distance(game.read(), (0, 0, 120)) <= .3
    assert nav.stats['plans'] <= 60 and nav.stats['map_builds'] <= 45
    assert len(game.events) < 60  # Baseline was 504 events and 239 plans.
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 1
    assert not game.keys and game.closed


def test_lookahead_does_not_cut_an_unseen_corner_in_a_remembered_path(tmp_path):
    data = manual_points([(2, 0, 2)])
    memory = RoutePathMemory(tmp_path/'route.json', data)
    assert memory.remember(0, [[0., 0., 0.], [0., 0., 1.], [0., 0., 2.], [1., 0., 2.], [2., 0., 2.]])
    nav = LocalNavigator(lambda: scene(), water_areas=(), path_memory=memory)
    nav.set_waypoint(0)
    target = nav.next_target((0, 0, 0), (2, 0, 2))
    assert target[0] == 0 and target[2] <= 2.


def test_missing_geometry_discards_old_corridor_instead_of_driving_memory_blindly(tmp_path):
    data = manual_points([(0, 0, 8)])
    memory = RoutePathMemory(tmp_path/'route.json', data)
    assert memory.remember(0, [[0., 0., float(z)] for z in range(9)])
    current = [scene()]
    nav = LocalNavigator(lambda: current[0], water_areas=(), path_memory=memory)
    nav.set_waypoint(0)
    nav.next_target((0, 0, 0), (0, 0, 8))
    current[0] = None
    with pytest.raises(NavigationWait) as waiting:
        nav.next_target((0, 0, 1), (0, 0, 8))
    assert waiting.value.reason == 'scene' and not nav.path


@pytest.mark.parametrize('jump', [False, True])
def test_recovery_steps_and_long_jumps_cannot_drive_into_known_water(jump):
    water = box(-100, 100, 1, 3)['polygon']
    game = NavigationGame(physical=[water])
    nav = LocalNavigator(lambda: scene(), clock=game.monotonic, water_areas=[water])
    follower = _Follower({'duration': 0., 'client_size': game.size()}, game, game, game, game,
                         lambda *a, **kw: None, game, waypoints=[{'position': [0, 0, 8]}],
                         navigator=nav, continuous_recovery=True, recovery_attempts=5)
    follower.recovery.begin(game.read())
    basis = {'forward': [0, 1], 'right': [1, 0]}
    if jump:
        follower.recovery_jump((0, 0, 8), basis, game.read())
        assert any(e == {'type': 'key_down', 'key': 'space'} for _, e in game.events)
    else:
        follower.recovery_leg((0, 0, 4), basis, game.read(), (0, 0, 8))
    assert game.read()[2] < .1 and game.collisions == 0 and not game.keys


def test_recovery_movement_is_released_if_scene_data_disappears_during_a_jump():
    game = Game()
    nav = LocalNavigator(lambda: scene() if game.time < .2 else None, clock=game.monotonic, water_areas=())
    follower = _Follower({'duration': 0., 'client_size': game.size()}, game, game, game, game,
                         lambda *a, **kw: None, game, waypoints=[{'position': [0, 0, 8]}],
                         navigator=nav, continuous_recovery=True, recovery_attempts=5)
    follower.recovery.begin(game.read())
    assert not follower.recovery_jump((0, 0, 8), {'forward': [0, 1], 'right': [1, 0]}, game.read())
    assert game.time <= .22 and not game.keys
