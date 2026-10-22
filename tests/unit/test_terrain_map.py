"""Verified terrain walls affect steering, recovery and retained map geometry."""
import math

from tests.unit.test_coordinate_navigation import NavigationGame, scene
from tests.unit.test_route_path_memory import run_walk
from tests.unit.test_coordinate_player import manual_points
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap, plan_path, polygon_distance
from utils.core.terrain_map import verified_terrain_walls, wall_geometry, terrain_walls
from utils.core.terrain_reference import TERRAIN_EDGES, TERRAIN_GAME_ASSEMBLY_SHA256
from utils.ui.map_draw_cache import SceneGeometryCache, Viewport


def wall():
    return wall_geometry((-3., 3., 3., 3., -2., 20.), 0)


def test_terrain_requires_verified_main_map_and_current_game_build():
    assert not verified_terrain_walls('foreign-build', True)
    assert not verified_terrain_walls(TERRAIN_GAME_ASSEMBLY_SHA256, False)
    assert verified_terrain_walls(TERRAIN_GAME_ASSEMBLY_SHA256, True) is terrain_walls()
    assert len(terrain_walls()) == 3464
    assert all(all(math.isfinite(v) for v in edge) and edge[4] < edge[5] for edge in TERRAIN_EDGES)


def test_current_identified_wall_is_in_the_verified_reference():
    p = (-134.92593383789062, 22.810710906982422, 108.16063690185547)
    nearby = [w for w in terrain_walls() if w['height'][0] < p[1] < w['height'][1]
              and polygon_distance((p[0], p[2]), (p[0], p[2]), w['polygon']) < 4.]
    assert nearby
    assert not any('address' in key for w in nearby for key in w)


def test_route_goes_around_vertical_wall_instead_of_through_it():
    data = scene();data['colliders'] = [wall()]
    obstacles = ObstacleMap(data, (0, 0, 0), water_areas=())
    assert not obstacles.clear((0, 0), (0, 8))
    path = plan_path((0, 0, 0), (0, 0, 8), obstacles)
    assert path and max(abs(p[0]) for p in path) > 3.
    before = (0, 0)
    for point in path:
        after = (point[0], point[2]);assert obstacles.clear(before, after);before = after
    assert not obstacles.approachable  # A recorded point never relaxes a mountain wall.


def test_terrain_below_feet_or_above_head_does_not_block_other_floor():
    data = scene();data['colliders'] = [wall()]
    assert ObstacleMap(data, (0, 21, 0), water_areas=()).clear((0, 0), (0, 8))
    assert ObstacleMap(data, (0, -5, 0), water_areas=()).clear((0, 0), (0, 8))


def test_jump_recovery_refuses_terrain_but_can_probe_an_ordinary_low_obstacle():
    data = scene();data['colliders'] = [wall()];data['terrain_revision'] = 'main-terrain-v1'
    nav = LocalNavigator(lambda: data, water_areas=())
    basis = {'forward': (0., 1.), 'right': (1., 0.)}
    keys = nav.recovery_keys((0, 0, 2), (0, 0, 8), basis, 2.)
    assert keys != {'w'} and keys != {'w', 'd'} and keys != {'w', 'a'}
    data = scene()
    assert nav.recovery_keys((0, 0, 2), (0, 0, 8), basis, 2.) == {'w'}


def test_full_replay_detours_without_collisions_or_skipping_collection():
    obstacle = wall();snapshot = scene();snapshot['colliders'] = [obstacle]
    game = NavigationGame(physical=[obstacle['polygon']])
    assert run_walk(game, manual_points([(0, 0, 8)]), None, snapshot)[0]
    assert game.collisions == 0 and not game.keys and game.closed
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 1


def test_map_uses_separate_terrain_style_and_reuses_geometry_on_zoom():
    data = {'objects': [], 'colliders': [wall()]}
    cache = SceneGeometryCache([], {})
    first = Viewport(600, 400, 0, 0, 5.)
    _, boxes = cache.select(data, (0, 0, 0), 150., first)
    assert len(boxes) == 1 and cache.barrier_kinds[boxes[0][0]] == 'mountain'
    count = cache.preparations
    cache.select(data, (0, 0, 0), 150., Viewport(600, 400, 0, 0, 10.))
    assert cache.preparations == count


def test_terrain_reference_requires_current_streaming_root_and_scene_name():
    from unittest.mock import Mock
    from utils.drivers.native_world_collisions import NativeWorldCollisions
    reader = NativeWorldCollisions.__new__(NativeWorldCollisions)
    reader.meta = Mock();reader.root_class = 10;reader.scene_key = (20,)
    reader.meta.static_ref.return_value = 20
    reader.meta.ref.return_value = 30
    reader.meta.string.return_value = 'startown'
    assert reader.main_terrain_loaded()
    reader.meta.string.return_value = 'microhomeland'
    assert not reader.main_terrain_loaded()
    reader.meta.string.return_value = 'startown'
    reader.meta.static_ref.side_effect = [20, 21]
    assert not reader.main_terrain_loaded()
