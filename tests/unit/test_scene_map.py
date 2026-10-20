"""Scene projections use explicit transforms, never inferred obstacle sizes."""
import math
import pytest
from utils.core.scene_map import box_geometry, compose_transform, select_geometry, select_water_areas
from utils.core.lake_areas import WATER_AREAS


def test_box_projects_its_real_size_and_center():
    box = box_geometry([10, 2, 20], [0, 0, 0, 1], [1, 1, 0], [4, 2, 6])
    assert set(map(tuple, box['polygon'])) == {(9, 17), (13, 17), (13, 23), (9, 23)}
    assert box['height'] == [2, 4]


def test_parent_rotation_and_scale_are_applied_to_local_box_data():
    parent = ([10, 2, 20], [0, math.sqrt(.5), 0, math.sqrt(.5)], 2.)
    box = box_geometry([1, 0, 0], [0, 0, 0, 1], [0, 0, 0], [2, 2, 4], parent)
    xs, zs = zip(*box['polygon'])
    assert (min(xs), max(xs), min(zs), max(zs)) == pytest.approx((6, 14, 16, 20))
    assert box['height'] == [0, 4]


def test_nested_transform_keeps_world_position_rotation_and_uniform_scale():
    parent = ([10, 0, 20], [0, 0, 0, 1], 2.)
    result = compose_transform(parent, ([1, 2, 3], [0, 0, 0, 1], .5))
    assert result == ([12., 4., 26.], [0., 0., 0., 1.], 1.)


@pytest.mark.parametrize('size', [[0, 1, 1], [-1, 1, 1], [math.nan, 1, 1]])
def test_invalid_dimensions_never_become_obstacles(size):
    with pytest.raises(ValueError):
        box_geometry([0, 0, 0], [0, 0, 0, 1], [0, 0, 0], size)


def test_map_selection_keeps_large_intersecting_boxes_and_limits_distant_objects():
    near = box_geometry([9, 0, 0], [0, 0, 0, 1], [0, 0, 0], [10, 1, 2])
    far = box_geometry([100, 0, 0], [0, 0, 0, 1], [0, 0, 0], [1, 1, 1])
    scene = {'objects': [{'position': [2, 0, 1]}, {'position': [100, 0, 0]}], 'colliders': [near, far]}
    objects, boxes = select_geometry(scene, [0, 0, 0], 5.)
    assert len(objects) == len(boxes) == 1 and boxes[0] == near


def test_reference_water_areas_are_world_aligned_and_select_only_visible_polygons():
    assert len(WATER_AREAS) >= 20
    assert all(len(area) >= 3 and all(math.isfinite(value) for vertex in area for value in vertex)
               for area in WATER_AREAS)
    # This is the pond immediately east of the captured player position.
    visible = select_water_areas(WATER_AREAS, -100, -75, 38, 60)
    assert visible
    assert all(area in WATER_AREAS for area in visible)
    assert not select_water_areas(WATER_AREAS, 400, 500, 400, 500)
    assert not select_water_areas(WATER_AREAS, 1, 1, 0, 10)
