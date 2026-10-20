"""Presentation projections must remain faithful to world position and segments."""
from unittest.mock import Mock
import pytest

from utils.ui.map_draw_cache import Viewport
from utils.ui.map_presentation import draw_chrome, draw_grid, movement_direction


@pytest.mark.parametrize('end,expected', [((1, 0), (1, 0)), ((0, 2), (0, -1)),
                                        ((-2, 0), (-1, 0)), ((0, -3), (0, 1))])
def test_movement_direction_uses_screen_axes(end, expected):
    trail = [{'x': 0, 'z': 0, 'segment': 1}, {'x': end[0], 'z': end[1], 'segment': 1}]
    assert movement_direction(trail) == pytest.approx(expected)


def test_movement_direction_never_connects_a_teleport_or_a_new_session():
    assert movement_direction([]) is None
    assert movement_direction([{'x': 1, 'z': 0, 'segment': 2}]) is None
    assert movement_direction([{'x': 0, 'z': 0, 'segment': 1},
                               {'x': 100, 'z': 0, 'segment': 2}]) is None


def test_scale_bar_uses_the_actual_world_projection_and_grid_stays_below_objects():
    canvas, viewport = Mock(), Viewport(1000, 600, 12, 34, 9)
    draw_grid(canvas, viewport, 10)
    canvas.tag_lower.assert_called_once_with('map-grid')
    assert canvas.create_line.call_count < 100
    draw_chrome(canvas, viewport, 10)
    bar = next(call for call in canvas.create_line.call_args_list
               if call.kwargs.get('tags') == 'map-scale' and call.kwargs.get('width') == 3)
    x1, z1, x2, z2 = bar.args
    assert x2-x1 == pytest.approx(90) and z1 == z2
