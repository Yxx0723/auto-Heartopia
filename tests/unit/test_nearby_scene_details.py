"""Display-only nearby detail; no game process or input."""
from utils.ui.map_draw_cache import SceneGeometryCache, Viewport


def select(scene, position=(0, 0, 0)):
    cache = SceneGeometryCache((), {})
    cache.select(scene, position, 150, Viewport(800, 600, 0, 0, 5))
    return cache, cache.nearby_details(position)


def test_unclassified_loaded_models_are_visible_only_inside_twenty_units():
    source = {'models': [{'position': [20, 0, 0], 'model_type': 'Building'},
                         {'position': [20.01, 0, 0], 'model_type': 'Building'}]}
    cache, (models, _) = select(source)
    assert len(models) == 1 and models[0]['position'][0] == 20
    assert cache.preparations == 1


def test_large_building_collision_included_when_its_center_is_outside_radius():
    box = {'polygon': [[19, -2], [60, -2], [60, 2], [19, 2]], 'height': [0, 8]}
    _, (_, boxes) = select({'colliders': [box]})
    assert len(boxes) == 1 and boxes[0][1] == box


def test_circle_query_excludes_polygon_with_near_aabb_but_distant_edges():
    box = {'polygon': [[19, 40], [40, 19], [40, 40]], 'height': [0, 8]}
    _, (_, boxes) = select({'colliders': [box]})
    assert not boxes


def test_coincident_footprints_on_different_heights_keep_distinct_details():
    polygon = [[-2, -2], [2, -2], [2, 2], [-2, 2]]
    _, (_, boxes) = select({'colliders': [{'polygon': polygon, 'height': [0, 2]},
                                        {'polygon': polygon, 'height': [8, 10]}]})
    assert len({key for key, _ in boxes}) == 2


def test_nearby_details_never_promote_cached_objects_into_live_models():
    _, (models, boxes) = select({'objects': [{'position': [1, 0, 1], 'category': 'other'},
                                           {'position': [2, 0, 2], 'category': 'tree'},
                                           {'position': [3, 0, 3], 'category': 'other', 'map_cached': True}],
                                'colliders': [{'polygon': [[0, 0], [1, 0], [1, 1]], 'map_cached': True}]})
    assert len(models) == 1 and models[0]['position'] == [1, 0, 1]
    assert not boxes


def test_scene_replacement_clears_models_and_detail_keys():
    cache, (models, _) = select({'models': [{'position': [1, 0, 0], 'model_type': 'Building'}]})
    assert models
    cache.select({}, (0, 0, 0), 150, Viewport(800, 600, 0, 0, 5))
    assert cache.nearby_details((0, 0, 0)) == ([], [])
    assert not any(key[0].startswith('nearby-') for key in cache.valid_keys)
