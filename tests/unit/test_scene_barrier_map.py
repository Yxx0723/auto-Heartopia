"""Fence drawing is cached; visibility never changes live navigation data."""
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_navigation import assert_clear, box, scene
from tests.unit.test_map_draw_cache import Canvas
from tests.unit.test_native_coordinate_monitor import capture_window
from utils.core.coordinate_navigation import ObstacleMap, plan_path, polygon_distance
from utils.core.scene_map import barrier_strokes, box_geometry
from utils.ui.map_draw_cache import MapDrawCache, Viewport
from utils.ui.native_coordinate_monitor import SCENE_RANKS


def barrier(kind='fence', **kwargs):
    return {**box(**kwargs), 'category': kind, 'source': 'verified-solid-collider'}


def test_fences_route_around_solid_parts_but_preserve_a_real_opening():
    closed = scene(barrier(left=-5, right=5))
    obstacles = ObstacleMap(closed, (0, 0, 0), water_areas=())
    path = plan_path((0, 0, 0), (0, 0, 8), obstacles)
    assert max(abs(p[0]) for p in path) > 5.6
    assert_clear(path, obstacles)
    opened = scene(barrier(left=-5, right=-1.5), barrier('handrail', left=1.5, right=5))
    obstacles = ObstacleMap(opened, (0, 0, 0), water_areas=())
    path = plan_path((0, 0, 0), (0, 0, 8), obstacles)
    assert all(p[0] == 0 for p in path) and path[-1] == (0., 0, 8.)
    assert_clear(path, obstacles)


def test_fence_below_the_player_or_overhead_does_not_block_another_floor():
    obstacles = ObstacleMap(scene(barrier(height=(-2, 0)), barrier('handrail', height=(3, 4))),
                            (0, 0, 0), water_areas=())
    path = plan_path((0, 0, 0), (0, 0, 8), obstacles)
    assert all(p[0] == 0 for p in path) and path[-1] == (0., 0, 8.)
    assert_clear(path, obstacles)


def test_rotated_and_sloping_barrier_strokes_stay_inside_the_actual_footprint():
    polygon = box_geometry([0, 0, 0], [.2, .4, .1, .88], [0, 0, 0], [8, 1.5, .25])['polygon']
    strokes = barrier_strokes(polygon)
    assert len(strokes) >= 3
    for a, b in strokes:
        for t in (0, .25, .5, .75, 1.):
            p = [a[i] + (b[i]-a[i])*t for i in range(2)]
            assert polygon_distance(p, p, polygon) < 1e-8
    long = barrier_strokes([[-10000, 0], [10000, 0], [10000, .1], [-10000, .1]])
    assert len(long) <= 66


def test_barrier_cache_survives_zoom_and_hidden_reentry_and_invalidates_stale_type():
    canvas, viewport = Canvas(), Viewport(600, 400, 0, 0, 8)
    cache = MapDrawCache(canvas)
    source = scene(barrier())
    def frame(view, show=True, data=source):
        cache.begin(view)
        _, boxes = cache.geometry.select(data, (0, 0, 0), 80, view)
        if show:
            for key, coords, _ in boxes:
                cache.barrier(key, coords, '#e6b96e', 'fence')
        cache.finish()
    frame(viewport)
    ids = set(canvas.items)
    original = {i: canvas.coords(i)[:] for i in ids}
    frame(Viewport(800, 500, 2, 3, 16))
    frame(viewport, show=False)
    assert all(i['state'] == 'hidden' for i in canvas.items.values())
    frame(viewport)
    assert set(canvas.items) == ids
    for i in ids:
        assert canvas.coords(i) == pytest.approx(original[i])
    frame(viewport, show=False, data=scene(box()))
    assert not canvas.items and not cache.geometry.barrier_kinds


@pytest.mark.parametrize('show_collision,show_barrier', [(False, True), (True, False), (False, False)])
def test_native_barrier_switch_is_independent_and_leaves_obstacles_intact(tmp_path, show_collision, show_barrier):
    window = capture_window(tmp_path)
    window.canvas = Mock()
    window.canvas.winfo_width.return_value = 800
    window.canvas.winfo_height.return_value = 500
    window.map_cache = MapDrawCache(window.canvas, ranks=SCENE_RANKS)
    window.draw_job, window.closed = None, False
    fence = barrier(left=-47, right=-44, low=130, high=130.2, height=(23, 25))
    rail = barrier('handrail', left=-44, right=-41, low=130, high=130.2, height=(23, 25))
    generic = box(-45, -44, 133, 134, (23, 25))
    window.scene_state = scene(fence, rail, generic)
    for name, value in [('objects', False), ('water', False), ('roads', False),
                        ('colliders', show_collision), ('barriers', show_barrier)]:
        variable = Mock()
        variable.get.return_value = value
        setattr(window, 'scene_' + name + '_var', variable)
    window.zoom, window.map_view = 1., 'nearby'
    window.fields = {'scale': Mock()}
    window.draw()
    tags = window.map_cache.visible_tags
    assert ('scene-fence' in tags) is show_barrier
    assert ('scene-handrail' in tags) is show_barrier
    assert ('scene-collider' in tags) is show_collision
    obstacles = ObstacleMap(window.scene_state, (-45.5, 23.25, 128.2), water_areas=())
    assert not obstacles.clear((-45.5, 128.2), (-45.5, 132))
    assert window.scene_state['colliders'] == [fence, rail, generic]
