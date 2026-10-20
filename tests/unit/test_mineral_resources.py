"""Mineral identities need exact live components, stable IDs and correct owners."""
import struct
from unittest.mock import Mock

import pytest

from tests.unit.test_mono_scene import shrub_runtime
from tests.unit.test_scene_display_cache import SceneDisplayCache, scene
from tests.unit.test_native_coordinate_monitor import category_window, drawn_object_tags
from utils.drivers.mono_scene import SceneMetadata
from utils.ui.native_coordinate_monitor import CoordinateWindow, SCENE_CATEGORIES


def mineral_runtime(item_id=302):
    r, reader, owner, entity, _, components = shrub_runtime()
    r.add_class('CollectableStoneComponent', 'XDTLevelAndEntity.Gameplay.Component.Gather',
                [('_itemTypeID', 52, 8)], parent='GatherComponent', size=56)
    component = r.make_object('CollectableStoneComponent', 56)
    r.p64(component+16, entity)
    r.write(component+52, struct.pack('<i', item_id))
    entries = struct.unpack('<Q', r.read(components+24, 8))[0]
    r.p64(entries+48, component)
    return r, reader, owner, entity, component, components


@pytest.mark.parametrize('item_id,expected', [(301, 'rock'), (302, 'ore'), (0, 'other'), (303, 'other'), (-1, 'other')])
def test_checked_mineral_ids_are_not_guessed_from_an_id_prefix(item_id, expected):
    _, reader, owner, *_ = mineral_runtime(item_id)
    assert reader.object_category(owner, reader.meta.obj(owner)) == expected


@pytest.mark.parametrize('fault', ['namespace', 'similar_name', 'owner', 'changed_owner', 'changed_id',
                                  'dictionary_version', 'item_type', 'item_offset'])
def test_foreign_changing_or_invalid_components_stay_generic(fault):
    r, reader, owner, entity, component, components = mineral_runtime()
    klass = r.classes['CollectableStoneComponent']
    if fault == 'namespace':
        r.p64(klass+0x50, r.string('Unrelated.Gather'))
    if fault == 'similar_name':
        r.p64(klass+0x48, r.string('CollectableStoneComponentProxy'))
    if fault == 'owner':
        r.p64(component+16, r.entity)
    fields = struct.unpack('<Q', r.read(klass+0xa0, 8))[0]
    if fault == 'item_type':
        field_type = struct.unpack('<Q', r.read(fields, 8))[0]
        r.write(field_type+10, b'\x0c')
    if fault == 'item_offset':
        r.write(fields+24, struct.pack('<i', 1000))

    def read(address, size):
        value = r.read(address, size)
        if fault == 'changed_owner' and address == component+16:
            r.p64(component+16, r.entity)
        if fault == 'changed_id' and address == component+52:
            r.write(component+52, struct.pack('<i', 0))
        if fault == 'dictionary_version' and address == component:
            r.write(components+76, struct.pack('<i', 1))
        return value
    reader.meta = SceneMetadata(read, r.domain)
    assert reader.object_category(owner, reader.meta.obj(owner)) == 'other'


def test_conflicting_bush_and_mineral_components_do_not_become_a_shrub():
    r, reader, owner, entity, _, components = mineral_runtime()
    bush = r.make_object('CollectableBushComponent', 56)
    r.p64(bush+16, entity)
    r.write(bush+52, struct.pack('<i', 102))
    entries = struct.unpack('<Q', r.read(components+24, 8))[0]
    r.write(entries+56+4, struct.pack('<i', -1))
    r.p64(entries+56+16, bush)
    assert reader.object_category(owner, reader.meta.obj(owner)) == 'other'


@pytest.mark.parametrize('category', ['rock', 'ore'])
def test_only_reader_verified_fixed_minerals_are_retained_and_near_absence_removes_them(category):
    cache = SceneDisplayCache()
    mineral = {'position': [140, 0, 0], 'category': category, 'map_static': True,
               'map_id': ('mineral', 1, 2)}
    cache.update(scene(mineral), 0.)
    assert cache.update(scene(x=300), 1.)['objects'] == [{**mineral, 'map_cached': True}]
    assert cache.update(scene(x=140), 2.)['objects'] == []
    cache.update(scene({k: v for k, v in mineral.items() if k != 'map_static'}), 3.)
    assert cache.update(scene(x=300), 4.)['objects'] == []


def test_rock_and_ore_have_different_icons_and_explicit_filter_labels():
    canvas = Mock()
    for category in ('rock', 'ore'):
        CoordinateWindow.draw_scene_symbol(canvas, 20, 30, category)
    polygons = canvas.create_polygon.call_args_list
    rock = [c for c in polygons if c.kwargs['tags'] == 'scene-rock']
    ore = [c for c in polygons if c.kwargs['tags'] == 'scene-ore']
    assert rock and ore and rock[0].kwargs['fill'] != ore[0].kwargs['fill']
    assert len(ore) > len(rock)  # A separate crystal silhouette, not only another tint.
    assert SCENE_CATEGORIES['rock'][0] == '岩石' and SCENE_CATEGORIES['ore'][0] == '矿石'


def test_mineral_filters_hover_and_saved_points_work_independently(tmp_path):
    window = category_window(tmp_path)
    window.map_hover = Mock()
    original = [point.copy() for point in window.saved_points]
    window.toggle_scene_category('rock')
    assert drawn_object_tags(window) == {'scene-rock'}
    assert [target.title for target in window.map_hover.update.call_args.args[1]] == ['岩石', '路线点 1']
    window.toggle_scene_category('ore')
    assert drawn_object_tags(window) == {'scene-rock', 'scene-ore'}
    window.toggle_scene_category('rock')
    assert drawn_object_tags(window) == {'scene-ore'}
    assert [target.title for target in window.map_hover.update.call_args.args[1]] == ['矿石', '路线点 1']
    window.set_scene_categories(False)
    assert not drawn_object_tags(window)
    assert window.saved_points == original
