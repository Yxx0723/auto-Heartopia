"""Presentation projections must remain faithful to world position and segments."""
from unittest.mock import Mock
import pytest

from utils.ui.map_draw_cache import Viewport
from utils.ui.map_presentation import draw_chrome, draw_grid, movement_direction, chrome_bounds


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


def test_grid_stays_below_objects_without_the_removed_map_decorations():
    canvas, viewport = Mock(), Viewport(1000, 600, 12, 34, 9)
    draw_grid(canvas, viewport, 10)
    canvas.tag_lower.assert_called_once_with('map-grid')
    assert canvas.create_line.call_count < 100
    draw_chrome(canvas, viewport, 10)
    assert not any(call.kwargs.get('tags') == 'map-scale' for call in canvas.create_line.call_args_list)
    texts = [call.kwargs.get('text', '') for call in canvas.create_text.call_args_list]
    assert '+X' not in texts and '+Z' not in texts
    assert not any('角色居中' in text or '单位' in text for text in texts)
    assert '角色' in texts and '点位' in texts  # The wide-view legend remains.
    assert chrome_bounds(viewport, 10) == [(673, 565, 988, 592)]


def test_narrow_map_draws_no_chrome_and_reserves_no_removed_overlay_space():
    canvas, viewport = Mock(), Viewport(490, 300, 12, 34, 9)
    draw_chrome(canvas, viewport, 10)
    assert not canvas.method_calls
    assert chrome_bounds(viewport, 10) == []
