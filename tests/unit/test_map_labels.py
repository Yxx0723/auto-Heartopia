"""Crowded routes must keep exact locations and readable, bounded labels."""
from utils.ui.map_draw_cache import Viewport
from unittest.mock import Mock
import pytest

from utils.ui.map_labels import layout_waypoints, HoverTarget, HoverIndex, MapHover, target_indicator


def point(number, x, z):
    return {'id': number, 'position': [x, 20., z]}


def overlaps(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def test_round_trip_keeps_first_and_last_number_at_the_same_location():
    points = [point(1, 0, 0), point(2, 20, 0), point(28, 0, 0)]
    labels = layout_waypoints(points, Viewport(600, 300, 0, 0, 3), active=3)
    shared = next(label for label in labels if len(label['indexes']) == 2)
    assert shared['indexes'] == (1, 3) and shared['caption'] == '1/28'
    assert shared['anchor'] == (300, 150) and shared['active']
    assert points == [point(1, 0, 0), point(2, 20, 0), point(28, 0, 0)]


def test_dense_labels_avoid_each_other_the_player_and_map_chrome():
    points = [point(i+1, i*2, 0) for i in range(40)]
    view = Viewport(600, 260, 0, 0, 2)
    reserved = [(260, 110, 340, 150), (0, 0, 150, 35)]
    labels = layout_waypoints(points, view, active=20, avoid=reserved)
    boxes = [label['rect'] for label in labels if label['rect']]
    assert next(label for label in labels if 20 in label['indexes'])['rect']
    assert all(not overlaps(a, b) for i, a in enumerate(boxes) for b in boxes[i+1:])
    assert all(not overlaps(a, b) for a in boxes for b in reserved)
    assert all(6 <= x1 < x2 <= 594 and 6 <= y1 < y2 <= 254 for x1, y1, x2, y2 in boxes)
    assert {i for label in labels for i in label['indexes']} == set(range(1, 41))


def test_zoomed_out_labels_and_hover_do_not_drop_coincident_points():
    labels = layout_waypoints([point(i, 0, 0) for i in range(1, 30)], Viewport(500, 250, 0, 0, .2))
    assert len(labels) == 1 and len(labels[0]['indexes']) == 29
    assert len(labels[0]['caption']) <= 8
    index = HoverIndex([HoverTarget(250, 125, '蓝莓', '距角色 3 单位'),
                        HoverTarget(250, 125, '路线点 1/28', '两个采集点', (260, 110, 300, 130))])
    assert index.pick(275, 120).title == '路线点 1/28'
    assert index.pick(250, 125).title == '路线点 1/28'
    assert index.pick(30, 30) is None


def test_target_indicator_is_bounded_and_does_not_claim_a_navigation_path():
    view = Viewport(600, 300, 10, 30, 3)
    assert target_indicator(view, [11, 20, 30]) is None
    x, y, dx, dy, distance = target_indicator(view, [1010, 20, 30])
    assert x == 576 and y == 150 and (dx, dy) == (1., 0.) and distance == 1000
    x, y, *_ = target_indicator(view, [-990, 20, 1030])
    assert 24 <= x <= 576 and 54 <= y <= 246


@pytest.mark.parametrize('pointer_y', [130, 149])
def test_hover_card_stays_inside_the_canvas_and_disappears_when_filtered_out(pointer_y):
    canvas = Mock()
    canvas.create_text.return_value = 1
    canvas.create_rectangle.return_value = 2
    canvas.bbox.return_value = (0, 0, 150, 48)
    hover = MapHover(canvas)
    hover.update(Viewport(240, 140, 0, 0, 3), [HoverTarget(230, pointer_y, '蓝莓', '平面距离 2.5 单位')])
    hover.show(230, pointer_y)
    x1, y1, x2, y2 = canvas.create_rectangle.call_args.args
    assert 6 <= x1 < x2 <= 234 and 6 <= y1 < y2 <= 134
    canvas.reset_mock()
    hover.update(hover.viewport, [])
    canvas.delete.assert_called_once_with('map-hover')
    canvas.create_text.assert_not_called()
    hover.hide()
    assert hover.pointer is None
