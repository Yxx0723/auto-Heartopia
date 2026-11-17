import pytest

from tests.unit.test_coordinate_navigation import box, scene
from utils.core.coordinate_navigation import ObstacleMap, plan_path


@pytest.mark.parametrize('category', ['tree', 'rock', 'wall', 'building'])
def test_solid_preferred_clearance_checks_entire_segment(category):
    solid = {**box(-1., 1., -1., 1.), 'category': category}
    obstacles = ObstacleMap(scene(solid), (-2, 0, 0), water_areas=())
    a, b = (-2., 1.65), (2., 1.65)
    assert obstacles.clear(a, b)
    assert obstacles.clearance_cost(a, b) > 0
    assert obstacles.clearance_cost(a, a) == 0
    assert obstacles.clearance_cost((-2., 1.8), (2., 1.8)) == 0
    assert not obstacles.clear((-2., 1.5), (2., 1.5))


def test_smoothed_path_keeps_clearance_and_prefers_space_at_corner():
    solid = box(-1., 1., -1., 1.)
    start, goal = (-3., 0., 1.65), (3., 0., 1.65)
    obstacles = ObstacleMap(scene(solid), start, water_areas=())
    path = plan_path(start, goal, obstacles)
    route = [(start[0], start[2]), *((p[0], p[2]) for p in path)]
    assert all(obstacles.clear(a, b) for a, b in zip(route, route[1:]))
    assert any(z > 1.75 for _, z in route)


def test_soft_clearance_does_not_disallow_narrow_corridor():
    obstacles = ObstacleMap(scene(box(-3, 3, -1, -.65), box(-3, 3, .65, 1)),
                            (-4, 0, 0), water_areas=())
    assert obstacles.clear((-4, 0), (4, 0))
    assert obstacles.clearance_cost((-4, 0), (4, 0)) > 0
