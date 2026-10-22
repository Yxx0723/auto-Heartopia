"""Retain static work while scene entities, input and window state stay live."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from utils.ui.map_draw_cache import SceneGeometryCache, Viewport
from utils.ui.hud_kit import HudReadout
from utils.ui.native_coordinate_monitor import CoordinateWindow


def test_identical_snapshot_reuses_both_indexes_but_fauna_updates_independently():
    scene = {'objects': [{'category': 'bird', 'position': [1., 0., 1.], 'available': True}],
             'colliders': [{'polygon': [[0, 0], [2, 0], [2, 2]]}],
             'bridge_surfaces': []}
    viewport = Viewport(400, 300, 0, 0, 3)
    cache = SceneGeometryCache((), {})
    cache.select(scene, (0, 0, 0), 150, viewport)
    boxes, cells, keys = cache.boxes, cache.box_cells, cache.valid_keys
    for index in range(5):
        scene = {**deepcopy(scene), 'sequence': index}
        cache.select(scene, (0, 0, 0), 150, viewport)
    assert cache.object_preparations == cache.box_preparations == 1
    assert cache.boxes is boxes and cache.box_cells is cells and cache.valid_keys is keys
    scene = deepcopy(scene)
    scene['objects'][0].update(position=[4., 0., 2.], available=False)
    objects, _ = cache.select(scene, (0, 0, 0), 150, viewport)
    assert objects[0]['position'] == [4., 0., 2.] and objects[0]['available'] is False
    assert cache.object_preparations == 2 and cache.box_preparations == 1
    scene = {**deepcopy(scene), 'objects': []}
    assert cache.select(scene, (0, 0, 0), 150, viewport)[0] == []
    assert cache.box_preparations == 1


def test_geometry_cache_detects_new_collision_bridge_and_cached_style():
    scene = {'objects': [], 'colliders': [], 'bridge_surfaces': []}
    cache = SceneGeometryCache((), {})
    viewport = Viewport(400, 300, 0, 0, 3)
    cache.select(scene, (0, 0, 0), 150, viewport)
    scene = deepcopy(scene)
    scene['bridge_surfaces'] = [{'polygon': [[0, 0], [2, 0], [2, 2]],
                                'category': 'bridge', 'source': 'bridge-mesh-reference'}]
    _, boxes = cache.select(scene, (0, 0, 0), 150, viewport)
    assert cache.barrier_kinds[boxes[0][0]] == 'bridge'
    old_key = boxes[0][0]
    scene = deepcopy(scene)
    scene['bridge_surfaces'][0]['map_cached'] = True
    _, boxes = cache.select(scene, (0, 0, 0), 150, viewport)
    assert boxes[0][0] in cache.cached_keys and old_key not in cache.valid_keys
    assert cache.object_preparations == 1 and cache.box_preparations == 3


def test_hidden_window_still_consumes_data_without_map_or_widget_work():
    ui = CoordinateWindow.__new__(CoordinateWindow)
    ui.canvas = Mock()
    ui.canvas.winfo_viewable.return_value = False
    ui.clock, ui.interacting_until = lambda: 1., 0.
    ui.draw = Mock()
    ui.closed, ui.draw_job = False, None
    ui.render()
    ui.draw_if_changed()
    ui.draw.assert_not_called()
    # render() must return before accessing any widgets/view, rather than
    # lowering or stopping coordinate/scene worker sampling.


def test_xyz_value_changes_update_only_text_until_the_cell_is_resized(monkeypatch):
    monkeypatch.setattr('utils.ui.hud_kit.mono_font', lambda *args: ('Consolas', 12))
    cell = SimpleNamespace(winfo_width=lambda: 100, winfo_height=lambda: 54,
                           _axis='X', _accent='#fff', _color='#f00', value=lambda: '1.0')
    for name in ('delete', 'create_rectangle', 'create_line', 'create_text', 'itemconfigure'):
        setattr(cell, name, Mock())
    cell.create_text.side_effect = iter(range(1, 20))
    HudReadout._draw(cell)
    number = cell._value_item
    for i in range(50):
        cell.value = lambda i=i: str(i)
        HudReadout._draw(cell)
    assert cell.delete.call_count == 1 and cell.create_rectangle.call_count == 1
    cell.itemconfigure.assert_called_with(number, text='49', fill='#f00')
    cell.winfo_width = lambda: 120
    HudReadout._draw(cell)
    assert cell.delete.call_count == 2
