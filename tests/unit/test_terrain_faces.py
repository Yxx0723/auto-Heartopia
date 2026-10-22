"""Actual cliff data and synthetic sloped faces, without process or input APIs."""
import math
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_navigation import NavigationGame, scene, assert_clear
from tests.unit.test_coordinate_player import manual_points
from utils.core.terrain_faces_reference import TERRAIN_TILTED_FACES
from utils.core.terrain_map import (triangle_geometry, terrain_slice, slope_degrees,
                                    terrain_walls, TERRAIN_GAME_ASSEMBLY_SHA256)
from utils.core.coordinate_navigation import ObstacleMap, LocalNavigator, plan_path, scene_geometry_signature
from utils.drivers.mono_scene import MemorySceneMap
from utils.flows.coordinate_player import drive_points
from utils.ui.map_draw_cache import SceneGeometryCache, Viewport
from utils.core.scene_display_cache import SceneDisplayCache


def tilted():
    return triangle_geometry(((-3., 0., 0.), (3., 0., 0.), (3., 10., 3.)), 99)


def test_all_new_faces_are_verified_near_vertical_without_replacing_original_edges():
    assert len(TERRAIN_TILTED_FACES) == 66
    assert len({index for index, _ in TERRAIN_TILTED_FACES}) == 66
    assert min(slope_degrees(t) for _, t in TERRAIN_TILTED_FACES) > 74
    assert max(slope_degrees(t) for _, t in TERRAIN_TILTED_FACES) <= 90
    faces = [w for w in terrain_walls() if 'terrain_triangle' in w]
    assert len(faces) == 66
    assert all(w['category'] == 'mountain' and w['polygon'] for w in faces)


@pytest.mark.parametrize('triangle', [
    ((-3, 0, 0), (3, 0, 0), (3, 0, 3)),
    ((-3, 0, 0), (3, 0, 0), (3, 3, 3)),
])
def test_floor_and_ordinary_ramp_are_not_turned_into_mountain_walls(triangle):
    assert triangle_geometry(triangle, 0) is None


@pytest.mark.parametrize('triangle', [(), ((0, 0, 0),)*3,
                                    ((0, 0, 0), (1, 2, 3), (math.nan, 0, 0))])
def test_invalid_faces_fail_without_a_guessed_bounding_box(triangle):
    with pytest.raises(ValueError):
        triangle_geometry(triangle, 0)


def test_character_height_slab_prevents_extruding_a_slanted_wall_over_other_floors():
    data = scene(tilted())
    low = ObstacleMap(data, (0, 0, 0), water_areas=())
    high = ObstacleMap(data, (0, 8, 0), water_areas=())
    assert low.clear((-5, 2.7), (5, 2.7))
    assert not high.clear((-5, 2.7), (5, 2.7))
    assert not low.approachable and not high.approachable


def test_triangle_slice_is_empty_below_and_above_and_never_moves_source_vertices():
    face = tilted()
    original = face['terrain_triangle']
    assert not terrain_slice(face, -5)
    assert not terrain_slice(face, 11)
    assert terrain_slice(face, 1)
    assert face['terrain_triangle'] == original


def test_astar_rechecks_all_sloped_face_shortcuts():
    face = tilted()
    start, target = (-5, 8, 2.7), (5, 8, 2.7)
    obstacles = ObstacleMap(scene(face), start, water_areas=())
    path = plan_path(start, target, obstacles)
    assert path[-1] == target
    assert_clear(path, obstacles, start=start)
    assert any(abs(p[2]-2.7) > .6 for p in path)


def test_closed_loop_replay_avoids_the_sliced_face_and_collects_only_at_goal():
    face = tilted()
    polygon = terrain_slice(face, 8)
    game = NavigationGame(physical=[polygon])
    game.position = [-5., 8., 2.7]
    nav = LocalNavigator(lambda: scene(face), clock=game.monotonic, water_areas=())
    assert drive_points(manual_points([(5., 8., 2.7)]), game.size(), lambda: game,
                        game, game, game, lambda *a, **k: None, clock=game,
                        navigator=nav, stable_navigation=True, skip_unreachable=True,
                        collect_wait_seconds=0.)
    assert game.collisions == 0 and not game.keys
    assert sum(e == {'type': 'key_down', 'key': 'f'} for _, e in game.events) == 1


def test_jump_recovery_cannot_bypass_a_verified_sloped_cliff():
    nav = LocalNavigator(lambda: scene(tilted()), water_areas=())
    basis = {'forward': (0., 1.), 'right': (1., 0.)}
    keys = nav.recovery_keys((0, 0, -1), (0, 0, 5), basis, 2.)
    assert keys != {'w'}


def test_height_slices_rebuild_after_a_small_vertical_change():
    nav = LocalNavigator(lambda: scene(tilted()), water_areas=())
    nav.next_target((-5, 0, 2.7), (5, 0, 2.7))
    first = nav.obstacles
    nav.next_target((-5, .3, 2.7), (5, .3, 2.7))
    assert nav.obstacles is not first and nav.obstacles.height == .3


def test_equal_xz_bounds_but_different_face_heights_change_the_geometry_signature():
    first = tilted()
    second = {**first, 'terrain_triangle': ((-3, 2, 0), (3, 2, 0), (3, 10, 3))}
    assert scene_geometry_signature(scene(first)) != scene_geometry_signature(scene(second))


def test_optional_resource_failure_cannot_hide_verified_main_terrain():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.memory = Mock(profile={'game_assembly_sha256': TERRAIN_GAME_ASSEMBLY_SHA256})
    reader.native = Mock()
    reader.native.main_terrain_loaded.return_value = True
    reader.global_minerals = Mock()
    reader.global_minerals.snapshot.side_effect = ValueError('optional scan unavailable')
    assert reader.terrain_geometry('ready') is terrain_walls()
    reader.global_minerals.snapshot.assert_not_called()
    assert not reader.terrain_geometry('unavailable')
    reader.native.main_terrain_loaded.return_value = False
    assert not reader.terrain_geometry('ready')


def test_world_transition_never_reuses_last_world_terrain():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.memory = Mock(profile={'game_assembly_sha256': TERRAIN_GAME_ASSEMBLY_SHA256})
    reader.native = Mock()
    reader.native.main_terrain_loaded.side_effect = ValueError('scene switching')
    assert not reader.terrain_geometry('ready')


def test_verified_static_mountain_is_visible_outside_nearby_radius_but_generic_box_is_not():
    cliff = triangle_geometry(((300, 0, 0), (306, 0, 0), (306, 10, 3)), 1)
    ordinary = {**cliff, 'source': 'verified-native-world-collider', 'category': 'other'}
    cache = SceneGeometryCache([], {})
    viewport = Viewport(800, 600, 0, 0, 1.)
    _, boxes = cache.select(scene(cliff, ordinary), (0, 0, 0), 150., viewport)
    assert len(boxes) == 1
    assert cache.barrier_kinds[boxes[0][0]] == 'mountain'
    count = cache.preparations
    cache.select(cache.source, (0, 0, 0), 150., Viewport(800, 600, 0, 0, .8))
    assert cache.preparations == count


def test_display_history_does_not_relabel_authoritative_far_terrain_as_an_old_sighting():
    cliff = triangle_geometry(((300, 0, 0), (306, 0, 0), (306, 10, 3)), 1)
    snapshot = {**scene(cliff), 'world_state': 'ready', 'cache_context': (77, 'main'), 'position': [0, 0, 0]}
    history = SceneDisplayCache()
    display = history.update(snapshot, 0)
    assert not display['colliders'][0].get('map_cached')
    cache = SceneGeometryCache([], {})
    _, boxes = cache.select(display, (0, 0, 0), 150., Viewport(800, 600, 0, 0, 1.))
    assert len(boxes) == 1 and history.count == 0


def test_lost_current_terrain_confirmation_does_not_show_a_historical_far_mountain():
    cliff = triangle_geometry(((300, 0, 0), (306, 0, 0), (306, 10, 3)), 1)
    snapshot = {**scene(cliff), 'world_state': 'ready', 'cache_context': (77, 'main'), 'position': [0, 0, 0]}
    history = SceneDisplayCache()
    history.update(snapshot, 0)
    latest = history.update({**snapshot, 'colliders': []}, 1)
    assert not latest['colliders']
