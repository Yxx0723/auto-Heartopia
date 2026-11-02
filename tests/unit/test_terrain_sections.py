"""Pinned source identity and conservative height sections; no grounded inference."""
from copy import deepcopy
import math

import pytest

from tests.unit.test_coordinate_navigation import scene, box
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap
from utils.core.guidance_navigation import GuidanceNavigator
from utils.core.terrain_map import terrain_walls
from utils.core.terrain_reference import TERRAIN_EDGES
from utils.core.terrain_sections import section_scene, section_polygon
from utils.core.terrain_wall_profiles import TERRAIN_WALL_PROFILES


def test_pinned_profiles_cover_known_edges_without_guessing_missing_matches():
    assert len(TERRAIN_WALL_PROFILES) == 3379
    for index, (edge, faces) in TERRAIN_WALL_PROFILES.items():
        assert tuple(TERRAIN_EDGES[index]) == edge
        assert faces and all(len(triangle) == 3 for _, triangle in faces)
    missing = [c for c in terrain_walls() if c['map_id'][1] not in TERRAIN_WALL_PROFILES]
    assert len(missing) == 19
    assert section_scene(scene(*missing), 12.)['colliders'] == missing


@pytest.mark.parametrize('change', ['identity', 'height', 'polygon', 'source', 'category'])
def test_modified_or_unknown_collider_never_uses_a_profile(change):
    collider = deepcopy(terrain_walls()[3232])
    if change == 'identity':
        collider['map_id'] = ('unknown', 3232)
    elif change == 'height':
        collider['height'] = (0., 100.)
    elif change == 'polygon':
        collider['polygon'] = tuple((x+.1, z) for x, z in collider['polygon'])
    else:
        collider[change] = 'unknown'
    assert section_scene(scene(collider), 12.4)['colliders'] == [collider]


def test_body_slice_keeps_real_vertical_faces_and_does_not_mutate_source():
    collider = terrain_walls()[3253]
    before = deepcopy(collider)
    output = section_scene(scene(collider), 12.4)['colliders']
    assert output and all(x['source'] == collider['source'] for x in output)
    assert collider == before
    midpoint = (245.875, 92.28125)
    obstacles = ObstacleMap(scene(*output), (245., 12.4, 92.), water_areas=())
    assert not obstacles.clear(midpoint, midpoint)


def test_body_section_is_not_whole_face_height_extrusion():
    triangle = ((0., 0., 0.), (0., 10., 0.), (10., 10., 0.))
    low = section_polygon(triangle, 0.)
    high = section_polygon(triangle, 7.)
    assert max(p[0] for p in low) == pytest.approx(2.5)
    assert max(p[0] for p in high) == pytest.approx(9.5)
    assert not section_polygon(triangle, 20.)


def test_conservative_section_covers_the_entire_unchanged_height_cache_band():
    triangle = ((0., 0., 0.), (0., 10., 0.), (10., 10., 0.))
    wide = section_polygon(triangle, 3.)
    # Body interval at heights 2.26..3.74 is within the section built at 3.
    assert min(p[0] for p in wide) <= 2.26+.25
    assert max(p[0] for p in wide) >= 3.74+1.75


@pytest.mark.parametrize('height', [math.nan, math.inf, True, None])
def test_invalid_height_never_silently_erases_collision(height):
    with pytest.raises(ValueError):
        section_scene(scene(*terrain_walls()[:1]), height)


def test_ordinary_navigation_still_uses_its_existing_terrain_policy():
    collider = terrain_walls()[3228]
    source = scene(collider)
    nav = LocalNavigator(lambda: source, water_areas=())
    nav._refresh_obstacles((245.88, 12.63, 91.11), (247.46, 12.63, 91.14))
    assert any(tuple(p) == collider['polygon'] for p, _ in nav.obstacles.polygons)


def test_guidance_recovery_uses_same_section_as_normal_planning():
    source = scene(*terrain_walls())
    nav = GuidanceNavigator(lambda: source, respect_water=False, water_areas=())
    position, target = (245.88, 12.63, 91.11), (247.46, 12.63, 91.14)
    point = nav.next_target(position, target)
    nav.recovery_keys(position, point, {'forward': [0., 1.], 'right': [1., 0.]}, .3)
    assert set(nav.obstacles.polygons) == set(nav.water_guard.polygons)


def test_unrelated_live_wall_is_preserved_without_profile_lookup():
    live = box()
    live['source'] = 'verified-native-world-collider'
    assert section_scene(scene(live), 0.)['colliders'] == [live]


@pytest.mark.parametrize('heights,index', [((13.6, 13.3), 3228), ((10.5, 10.7), 3232)])
def test_crossing_source_height_filter_rebuilds_before_normal_height_threshold(heights, index):
    source = scene(terrain_walls()[index])
    nav = GuidanceNavigator(lambda: source, respect_water=False, water_areas=())
    a, b = heights
    nav._refresh_obstacles((245., a, 90.), (247., a, 91.))
    old = nav.obstacles
    assert not old.polygons
    nav._refresh_obstacles((245., b, 90.), (247., b, 91.))
    assert nav.obstacles is not old and nav.obstacles.polygons
    nav._refresh_obstacles((245., a, 90.), (247., a, 91.))
    assert not nav.obstacles.polygons


def test_small_height_change_with_same_classification_reuses_conservative_map():
    source = scene(terrain_walls()[3232])
    nav = GuidanceNavigator(lambda: source, respect_water=False, water_areas=())
    nav._refresh_obstacles((243., 12.4, 87.), (244., 12.4, 88.))
    old = nav.obstacles
    nav._refresh_obstacles((243., 12.45, 87.), (244., 12.45, 88.))
    assert nav.obstacles is old


def test_recovery_cache_rebuilds_when_previously_skipped_ground_enters_body_band():
    source = scene(terrain_walls()[3228])
    nav = GuidanceNavigator(lambda: source, respect_water=False, water_areas=())
    basis = {'forward': [0., 1.], 'right': [1., 0.]}
    nav.recovery_keys((245., 13.6, 90.), (247., 13.6, 91.), basis, .3)
    old = nav.water_guard
    assert not old.polygons
    nav.recovery_keys((245., 13.3, 90.), (247., 13.3, 91.), basis, .3)
    assert nav.water_guard is not old and nav.water_guard.polygons


@pytest.mark.parametrize('prepared', [False, True])
@pytest.mark.parametrize('swapped', [False, True])
def test_changed_profile_identity_cannot_reuse_old_sliced_map(prepared, swapped):
    from utils.core.prepared_navigation import PreparedNavigationScene
    colliders = [deepcopy(terrain_walls()[i]) for i in (3228, 3229)]
    current = [scene(*colliders)]
    if prepared:
        current[0] = PreparedNavigationScene(current[0])
    nav = GuidanceNavigator(lambda: current[0], respect_water=False, water_areas=())
    position, target = (245.88, 12.63, 91.11), (247.46, 12.63, 91.14)
    nav._refresh_obstacles(position, target)
    old = nav.obstacles
    changed = deepcopy(colliders)
    if swapped:
        changed[0]['map_id'], changed[1]['map_id'] = changed[1]['map_id'], changed[0]['map_id']
    else:
        changed[0]['map_id'] = ('unknown', 3228)
    current[0] = scene(*changed)
    if prepared:
        current[0] = PreparedNavigationScene(current[0])
    nav._refresh_obstacles(position, target)
    assert nav.obstacles is not old
    assert any(tuple(p) == colliders[0]['polygon'] for p, _ in nav.obstacles.polygons)


def test_recovery_also_discards_sliced_map_when_profile_identity_disappears():
    collider = deepcopy(terrain_walls()[3228])
    current = [scene(collider)]
    nav = GuidanceNavigator(lambda: current[0], respect_water=False, water_areas=())
    p, aim = (245.88, 12.63, 91.11), (247.46, 12.63, 91.14)
    basis = {'forward': [0., 1.], 'right': [1., 0.]}
    nav.recovery_keys(p, aim, basis, .3)
    old = nav.water_guard
    current[0] = scene({**collider, 'map_id': ('unknown', 3228)})
    nav.recovery_keys(p, aim, basis, .3)
    assert nav.water_guard is not old
    assert any(tuple(poly) == collider['polygon'] for poly, _ in nav.water_guard.polygons)
