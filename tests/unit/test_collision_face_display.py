"""Geometric display orientation never establishes walking ability."""
import math
from copy import deepcopy
import pytest
from utils.core.scene_map import box_geometry


def faces(box):
    from utils.ui.collision_display import collision_faces
    return collision_faces(box)


def test_box_retains_world_corners_for_display_without_changing_old_projection():
    box = box_geometry([0, 1, 0], [0, 0, 0, 1], [0, 0, 0], [4, 2, 6])
    assert len(box['display_box']['corners']) == 8
    assert box['height'] == [0, 2]
    assert len(box['polygon']) == 4


def test_flat_box_has_one_candidate_top_and_four_side_faces():
    result = faces(box_geometry([0, 1, 0], [0, 0, 0, 1], [0, 0, 0], [4, 2, 6]))
    assert [kind for kind, _ in result].count('top') == 1
    assert [kind for kind, _ in result].count('side') == 4


def test_rotated_box_uses_transformed_faces_instead_of_projected_bounds():
    angle = math.radians(30)/2
    box = box_geometry([0, 1, 0], [0, 0, math.sin(angle), math.cos(angle)], [0, 0, 0], [4, 2, 6])
    tops = [polygon for kind, polygon in faces(box) if kind == 'top']
    assert len(tops) == 1
    assert set(tops[0]) != set(map(tuple, box['polygon']))


@pytest.mark.parametrize('bad', [None, {}, {'schema': 'oriented-box-v1', 'corners': [[0, 0, 0]]*8},
                               {'schema': 'oriented-box-v1', 'corners': [[math.nan, 0, 0]]*8}])
def test_missing_or_malformed_box_is_unknown_without_fabricating_a_top(bad):
    box = {'polygon': [[0, 0], [1, 0], [1, 1]], 'height': [0, 1], 'display_box': bad}
    assert faces(box) is None


def test_projection_only_or_cached_geometry_is_unknown():
    box = box_geometry([0, 0, 0], [0, 0, 0, 1], [0, 0, 0], [2, 2, 2])
    assert faces({**box, 'map_cached': True}) is None
    assert faces({'polygon': box['polygon'], 'height': box['height']}) is None


def test_display_classification_does_not_mutate_navigation_geometry():
    box = box_geometry([0, 0, 0], [0, 0, 0, 1], [0, 0, 0], [2, 2, 2])
    before = deepcopy(box)
    assert faces(box)
    assert box == before


def render(cache, scene):
    from utils.ui.map_draw_cache import Viewport
    cache.begin(Viewport(800, 600, 0, 0, 5))
    cache.geometry.select(scene, [0, 0, 0], 150, cache.viewport)
    for key, box in cache.geometry.nearby_details([0, 0, 0])[1]:
        cache.collision_detail(key, box)
    cache.finish()


def make_cache():
    from tests.unit.test_map_draw_cache import Canvas
    from utils.ui.map_draw_cache import MapDrawCache
    class StyledCanvas(Canvas):
        def create(self, *coords, **options):
            item = super().create(*coords, **options)
            self.items[item].update({key: value for key, value in options.items() if key != 'tags'})
            return item
        create_polygon = create_line = create_oval = create
    canvas = StyledCanvas()
    return canvas, MapDrawCache(canvas)


def test_unknown_outline_is_dashed_and_never_filled():
    canvas, cache = make_cache()
    render(cache, {'colliders': [{'polygon': [[0, 0], [1, 0], [1, 1]], 'height': [0, 1]}]})
    assert 'collision-unknown' in cache.visible_tags
    assert 'collision-top' not in cache.visible_tags
    assert all(item['dash'] == (4, 3) and item['fill'] == '' for item in canvas.items.values())


def test_new_box_geometry_replaces_old_unknown_style_and_reuses_unchanged_frame():
    canvas, cache = make_cache()
    box = box_geometry([0, 1, 0], [0, 0, 0, 1], [0, 0, 0], [4, 2, 6])
    render(cache, {'colliders': [{k: v for k, v in box.items() if k != 'display_box'}]})
    old_items = set(canvas.items)
    source = {'colliders': [box]}
    render(cache, source)
    assert not old_items & set(canvas.items)
    assert {'collision-top', 'collision-side'} <= cache.visible_tags
    assert 'collision-unknown' not in cache.visible_tags
    assert any(item.get('fill') == '#bfdac0' and item.get('stipple') == 'gray25' for item in canvas.items.values())
    created = canvas.created
    for _ in range(10):
        render(cache, source)
    assert canvas.created == created
    render(cache, {})
    assert not canvas.items


def test_extra_display_data_does_not_change_navigation_geometry_signature():
    from utils.core.coordinate_navigation import scene_geometry_signature
    box = box_geometry([0, 1, 0], [0, 0, 0, 1], [0, 0, 0], [4, 2, 6])
    old = {k: v for k, v in box.items() if k != 'display_box'}
    assert scene_geometry_signature({'colliders': [box]}) == scene_geometry_signature({'colliders': [old]})
