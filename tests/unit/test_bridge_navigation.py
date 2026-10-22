"""Bridge links never remove water or bypass physical obstacles."""
from copy import deepcopy
import math

import pytest

from utils.core.bridge_map import verified_bridge_data
from utils.core.bridge_navigation import BridgeSupport, capsule_intervals, polygon_intervals
from utils.core.bridge_reference import BRIDGE_MESH_FACES, SURVEYED_BRIDGE_PATHS
from utils.core.coordinate_navigation import (ObstacleMap, LocalNavigator, NavigationWait,
                                             plan_path, scene_geometry_signature)
from utils.core.terrain_reference import TERRAIN_GAME_ASSEMBLY_SHA256
from utils.ui.map_draw_cache import SceneGeometryCache, Viewport
from tests.unit.test_coordinate_navigation import NavigationGame
from tests.unit.test_coordinate_player import manual_points
from utils.flows.coordinate_player import drive_points

WATER = ((-2., -20.), (2., -20.), (2., 20.), (-2., 20.))
PATH = tuple((float(x), 10., 0.) for x in range(-4, 5))


def scene(path=PATH, **changes):
    return {'state': 'ready', 'world_state': 'ready', 'colliders': [],
            'cache_context': ('test', 1), 'bridge_revision': 'test-survey',
            'bridge_crossings': [{'points': path, 'source': 'surveyed-walking-bridge'}], **changes}


def test_analytic_capsule_handles_crossing_parallel_tangent_and_stationary_segments():
    assert capsule_intervals((-2, 0), (2, 0), (0, -1), (0, 1), .5) == [( .375, .625)]
    assert capsule_intervals((0, -2), (0, 2), (0, -1), (0, 1), .5) == [(.125, .875)]
    assert capsule_intervals((0, 0), (0, 0), (0, -1), (0, 1), .5) == [(0., 1.)]
    assert not capsule_intervals((2, 2), (2, 2), (0, -1), (0, 1), .5)
    assert capsule_intervals((-.5, 1.5), (.5, 1.5), (0, -1), (0, 1), .5) == [(.5, .5)]


def test_water_interval_includes_shore_margin_and_interior():
    assert polygon_intervals((-5, 0), (5, 0), WATER, 1.) == [(.2, .8)]
    assert polygon_intervals((0, 0), (0, 0), WATER, 1.) == [(0., 1.)]


def test_survey_allows_water_only_on_the_bridge_in_both_directions():
    obstacles = ObstacleMap(scene(), (-5, 10, 0), water_areas=(WATER,))
    assert obstacles.clear((-5, 0), (5, 0))
    assert obstacles.clear((5, 0), (-5, 0))
    assert not obstacles.clear((-5, 2), (5, 2))
    assert not obstacles.clear((0, 0), (0, 2))


def test_water_support_rejects_a_sub_sample_gap_in_the_bridge():
    data = scene()
    data['bridge_crossings'] = [{'source': 'surveyed-walking-bridge', 'points': p} for p in (
        ((-4., 10., 0.), (-3., 10., 0.), (-2., 10., 0.), (-1., 10., 0.), (-.4501, 10., 0.)),
        ((.4501, 10., 0.), (1., 10., 0.), (2., 10., 0.), (3., 10., 0.), (4., 10., 0.)))]
    assert not ObstacleMap(data, (-5, 10, 0), water_areas=(WATER,)).clear((-5, 0), (5, 0))


@pytest.mark.parametrize('change', [
    {'world_state': 'unavailable'}, {'bridge_revision': None},
    {'bridge_crossings': [{'points': PATH, 'source': 'name-only'}]},
    {'bridge_crossings': [{'points': PATH, 'source': 'surveyed-walking-bridge', 'map_cached': True}]},
    {'bridge_crossings': [{'points': ((-4, 10, 0), (4, 10, 0)), 'source': 'surveyed-walking-bridge'}]},
    {'bridge_crossings': [{'points': ((0, math.nan, 0), (1, 10, 0)), 'source': 'surveyed-walking-bridge'}]},
])
def test_stale_unowned_or_discontinuous_links_never_open_water(change):
    assert not ObstacleMap(scene(**change), (-5, 10, 0), water_areas=(WATER,)).clear((-5, 0), (5, 0))


def test_swimming_or_ground_below_bridge_cannot_use_the_upper_link():
    assert not ObstacleMap(scene(), (0, 6, 0), water_areas=(WATER,)).clear((0, 0), (1, 0))


@pytest.mark.parametrize('category', ['fence', 'handrail', 'mountain', 'other'])
def test_bridge_never_overrides_physical_barriers(category):
    barrier = {'polygon': ((-.1, -2), (.1, -2), (.1, 2), (-.1, 2)),
               'height': (10., 12.), 'category': category}
    obstacles = ObstacleMap(scene(colliders=[barrier]), (-5, 10, 0), water_areas=(WATER,))
    assert not obstacles.clear((-5, 0), (5, 0))


def test_a_star_uses_explicit_links_when_a_narrow_curved_bridge_misses_grid_nodes():
    path = ((-4., 10., .21), (-3., 10., .21), (-2., 10., .21), (-1., 10., .71),
            (0., 10., 1.21), (1., 10., .71), (2., 10., .21), (3., 10., .21), (4., 10., .21))
    start, target = (-5., 10., .21), (5., 10., .21)
    obstacles = ObstacleMap(scene(path), start, water_areas=(WATER,))
    assert not obstacles.clear((start[0], start[2]), (target[0], target[2]))
    planned = plan_path(start, target, obstacles, allow_partial=False)
    assert planned[-1] == target and any(p[2] > .7 for p in planned)
    for a, b in zip([start, *planned], planned):
        assert obstacles.clear((a[0], a[2]), (b[0], b[2]))


def test_recovery_guard_preserves_a_survey_but_blocks_leaving_the_bridge():
    nav = LocalNavigator(scene, water_areas=(WATER,))
    basis = {'forward': (1., 0.), 'right': (0., 1.)}
    assert nav.recovery_keys((0, 10, 0), (3, 10, 0), basis, .25) == {'w'}
    assert nav.recovery_keys((0, 10, 0), (0, 10, 3), basis, 1.) != {'d'}


@pytest.mark.parametrize('angle', [0., math.pi/3, -math.pi/2])
def test_feedback_replay_crosses_a_narrow_bridge_with_rotated_camera(angle):
    game = NavigationGame(angle=angle)
    game.position = [-5., 10., 0.]
    nav = LocalNavigator(scene, clock=game.monotonic, water_areas=(WATER,))
    phases = []
    assert drive_points(manual_points([(5., 10., 0.)]), game.size(), lambda: game,
                        game, game, game, lambda state, *a, **kw: phases.append(state),
                        clock=game, navigator=nav, stable_navigation=True,
                        skip_unreachable=True, collect_wait_seconds=0.)
    assert 'recovering' not in phases
    assert sum(event == {'type': 'key_down', 'key': 'f'} for _, event in game.events) == 1
    assert not game.keys and game.closed


def test_geometry_signature_tracks_removal_and_permission_changes():
    original = scene_geometry_signature(scene())
    assert original != scene_geometry_signature(scene(world_state='unavailable'))
    stale = scene()
    stale['bridge_crossings'][0]['map_cached'] = True
    assert original != scene_geometry_signature(stale)


def test_recovery_guard_drops_exemption_when_world_confirmation_disappears():
    data = scene()
    nav = LocalNavigator(lambda: data, water_areas=(WATER,))
    basis = {'forward': (1., 0.), 'right': (0., 1.)}
    assert nav.recovery_keys((0, 10, 0), (3, 10, 0), basis, .25) == {'w'}
    data = scene(world_state='unavailable')
    assert nav.recovery_keys((0, 10, 0), (3, 10, 0), basis, .25) != {'w'}


def test_replacing_live_snapshot_removes_old_water_exemption():
    data = scene()
    nav = LocalNavigator(lambda: data, water_areas=(WATER,))
    nav.next_target((-5, 10, 0), (5, 10, 0))
    data = scene(world_state='unavailable', bridge_crossings=[])
    try:
        nav.next_target((-5, 10, 0), (5, 10, 0))
    except NavigationWait:
        pass
    assert not nav.obstacles.clear((-5, 0), (5, 0))


def test_real_survey_is_continuous_and_not_just_its_two_endpoints():
    assert len(SURVEYED_BRIDGE_PATHS) == 1
    path = SURVEYED_BRIDGE_PATHS[0][1]
    assert 30 <= len(path) <= 128
    assert max(math.dist(a, b) for a, b in zip(path, path[1:])) < .5
    assert max(p[1] for p in path)-min(p[1] for p in path) < .5
    assert math.dist(path[0], path[-1]) > 14.
    assert BridgeSupport(scene(path), path[0]).paths


def test_mesh_reference_is_display_only_and_version_world_gated():
    surfaces, paths = verified_bridge_data(TERRAIN_GAME_ASSEMBLY_SHA256, True)
    assert len(BRIDGE_MESH_FACES) == 7 and sum(len(faces) for _, faces in BRIDGE_MESH_FACES) == 1199
    assert len(surfaces) > 1199 and len(paths) == 1
    assert all(s.get('source') == 'bridge-mesh-reference' for s in surfaces)
    assert verified_bridge_data('unknown', True) == ((), ())
    assert verified_bridge_data(TERRAIN_GAME_ASSEMBLY_SHA256, False) == ((), ())


def test_bridge_display_survives_radius_culling_without_entering_collision_list():
    reference = {'polygon': ((299, -1), (301, -1), (301, 1), (299, 1)),
                 'category': 'bridge', 'source': 'bridge-mesh-reference'}
    data = scene(bridge_surfaces=[reference], objects=[])
    before = deepcopy(data)
    cache = SceneGeometryCache((), {})
    _, shown = cache.select(data, (0, 10, 0), 150, Viewport(800, 600, 300, 0, 10))
    assert len(shown) == 1 and cache.barrier_kinds[shown[0][0]] == 'bridge'
    assert not data['colliders'] and data == before
    cache.select(scene(objects=[]), (0, 10, 0), 150, Viewport(800, 600, 300, 0, 10))
    assert not cache.boxes
