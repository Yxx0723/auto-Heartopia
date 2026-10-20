"""Reproduce orange leg 6 -> 7 and the reported stuck river-bank position."""
import math

import pytest

from tests.unit.test_coordinate_navigation import NavigationGame, scene
from tests.unit.test_coordinate_player import manual_points
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap, plan_path, polygon_distance
from utils.core.coordinate_route import distance
from utils.core.lake_areas import WATER_AREAS
from utils.core.navigation_water import NORTHERN_RIVER_AREAS
from utils.flows.coordinate_player import drive_points


POINT6 = (-114.1554730166, 20.4004859924, 128.5999342550)
POINT7 = (-127.0882805307, 23.0504856110, 40.7935240737)
STUCK = (-121.45737, 19.52226, 119.65246)


def test_old_navigation_allowed_the_reported_river_crossing():
    assert ObstacleMap(scene(), STUCK, water_areas=WATER_AREAS).clear(
        (STUCK[0], STUCK[2]), (-123., 96.), escape=True)
    assert not ObstacleMap(scene(), STUCK).clear(
        (STUCK[0], STUCK[2]), (-123., 96.), escape=True)


@pytest.mark.parametrize('start', [POINT6, STUCK])
def test_river_detour_preserves_the_dry_crossing_and_avoids_both_water_polygons(start):
    assert len(NORTHERN_RIVER_AREAS) == 2
    obstacles = ObstacleMap(scene(), start)
    path = plan_path(start, POINT7, obstacles)
    # Either the dry road gap to the west or land beyond the eastern tip;
    # fresh physical colliders decide which detour is actually available.
    assert min(p[0] for p in path) < -136. or max(p[0] for p in path) > -99.
    previous = start
    for point in path:
        a, b = (previous[0], previous[2]), (point[0], point[2])
        assert obstacles.clear(a, b, escape=True)
        assert all(polygon_distance(a, b, polygon) > 0 for polygon in NORTHERN_RIVER_AREAS)
        previous = point
    assert path[-1][2] < 110.  # Reaches the southern bank rather than stopping before the river.


@pytest.mark.parametrize('angle', [0., math.pi / 3, -math.pi / 2])
@pytest.mark.parametrize('start', [POINT6, STUCK])
def test_closed_loop_crosses_river_collects_only_at_original_destination_and_releases_keys(angle, start):
    game = NavigationGame(angle, physical=NORTHERN_RIVER_AREAS)
    game.position[:] = start
    # The simulated floor is flat. XZ is the real route; live Y comes from the game.
    target = (POINT7[0], start[1], POINT7[2])
    nav = LocalNavigator(lambda: scene(), clock=game.monotonic)
    collected, positions, calibration_collisions = [], [], []

    def report(state, message, **extra):
        positions.append(game.read())
        if state == 'playing' and not calibration_collisions:
            calibration_collisions.append(game.collisions)
        if state == 'collecting':
            collected.append((extra['waypoint'], game.read()))

    assert drive_points(manual_points([target]), game.size(), lambda: game, game, game, game,
                        report, clock=game, navigator=nav, collect_wait_seconds=0.)
    assert min(p[0] for p in positions) < -135.5 or max(p[0] for p in positions) > -99.5
    assert len(collected) == 1 and collected[0][0] == 1
    assert distance(collected[0][1], target) <= .3
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 1
    assert game.collisions == calibration_collisions[0] and not game.keys and game.closed


def test_manual_stop_during_river_detour_does_not_collect_or_leave_keys_held():
    game = NavigationGame(physical=NORTHERN_RIVER_AREAS)
    game.position[:] = POINT6
    game.stop_at = 4.
    nav = LocalNavigator(lambda: scene(), clock=game.monotonic)
    assert not drive_points(manual_points([POINT7]), game.size(), lambda: game, game, game, game,
                            lambda *a, **kw: None, clock=game, navigator=nav)
    assert not any(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events)
    assert not game.keys and game.closed
