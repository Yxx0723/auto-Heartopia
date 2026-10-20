"""Full-map reference symbols must survive range limits without entering navigation."""
from unittest.mock import Mock
from types import SimpleNamespace
import struct
import pytest

from utils.core.map_resources import combine_resource_markers
from utils.ui.map_draw_cache import MapDrawCache, Viewport
from utils.drivers.global_minerals import GlobalMineralReader
from utils.drivers.mono_scene import SceneMetadata
from tests.unit.test_mono_scene import dictionary
from tests.unit.test_native_coordinate_monitor import category_window


def definition_runtime():
    r, registry = dictionary()
    ns = 'EcsClient.XDT.Scene.Shared.Data.ServerData'
    for name, namespace, fields, size in (
        ('GameWorld', 'XDTGame.Core', [('_currentLevel', 0, 18, 17)], 16),
        ('LevelNode', 'XDTGame.Core', [('_instance', 16, 18)], 24),
        ('GameLevel_Main', 'XDTGameSystem.GameLevels', [], 16),
        ('ScriptEngine', 'MonoGame.ScriptFramework', [('<Instance>k__BackingField', 0, 18, 17), ('_holdingObjs', 16, 21)], 24),
        ('DynamicMonoBehaviour', '', [('ImplInternal', 16, 18)], 24),
        ('MainGameLevelConfig', 'XDTDataAndProtocol.Config.GameLevel', [('_behaviour', 16, 18), ('CollectableResourcePointAssets', 24, 18)], 32),
        ('CollectableResourcePointAssetsConfig', ns, [('points', 16, 18)], 24),
        ('ResType', ns, [('value__', 16, 8)], 20),
        ('CollectableResourcePointAsset', ns, [('resourceId', 16, 9), ('itemTypeId', 20, 8), ('resType', 24, 17), ('position', 28, 17)], 72),
        ('List`1', 'System.Collections.Generic', [('_items', 16, 29), ('_size', 24, 8), ('_version', 28, 8)], 32),
        ('ResourcePoint[]', '', [], 16),
    ):
        r.add_class(name, namespace, fields, size=size)
    fields = struct.unpack('<Q', r.read(r.classes['CollectableResourcePointAsset']+0xa0, 8))[0]
    typ = struct.unpack('<Q', r.read(fields+64, 8))[0]
    r.p64(typ, r.classes['ResType'])
    node = r.make_object('LevelNode', 24)
    level = r.make_object('GameLevel_Main', 16)
    engine = r.make_object('ScriptEngine', 24)
    wrapper = r.make_object('DynamicMonoBehaviour', 24)
    config = r.make_object('MainGameLevelConfig', 32)
    assets = r.make_object('CollectableResourcePointAssetsConfig', 24)
    points = r.make_object('List`1', 32)
    array = r.make_object('ResourcePoint[]', 48)
    r.write(r.classes['ResourcePoint[]']+0x1b, b'\x05')
    r.p64(r.classes['ResourcePoint[]'], r.classes['CollectableResourcePointAsset'])
    r.p64(array+24, 2)
    rows = []
    for i, (item_id, x) in enumerate(((301, -500.), (302, 400.))):
        point = r.make_object('CollectableResourcePointAsset', 72)
        r.write(point+16, struct.pack('<Iii3f', 100+i, item_id, 2, x, 20., 100.))
        r.p64(array+32+i*8, point)
        rows.append(point)
    for address, value in ((node+16, level), (engine+16, registry), (wrapper+16, config),
                           (config+16, wrapper), (config+24, assets), (assets+16, points), (points+16, array)):
        r.p64(address, value)
    r.write(points+24, struct.pack('<ii', 2, 1))
    entries = struct.unpack('<Q', r.read(registry+24, 8))[0]
    r.p64(entries+48, wrapper)
    for name, obj in (('GameWorld', node), ('ScriptEngine', engine)):
        storage = r.alloc(8)
        r.p64(storage, obj)
        r.p64(r.slots[name]+0x70, storage)
    now = [0.]
    reader = GlobalMineralReader(SceneMetadata(r.read, r.domain), r.base, {301: 'rock', 302: 'ore'},
                                 clock=lambda: now[0], classes=(r.classes['GameWorld'], r.classes['ScriptEngine']))
    r.parts = SimpleNamespace(node=node, level=level, engine=engine, registry=registry, config=config,
                              assets=assets, points=points, array=array, rows=rows, entries=entries)
    return r, reader, now


def test_typed_registered_main_map_definitions_include_far_unloaded_minerals_and_are_cached():
    _, reader, _ = definition_runtime()
    result = reader.snapshot()
    assert [(p['category'], p['position'][0]) for p in result] == [('rock', -500.), ('ore', 400.)]
    assert all(p['map_definition'] for p in result)
    assert reader.snapshot() is result and reader.loads == 1


@pytest.mark.parametrize('fault', ['domain', 'other_level', 'removed_registry', 'nan', 'duplicate_id',
                                  'point_type', 'position_type', 'item_code', 'position_offset'])
def test_invalid_definitions_do_not_leak_old_or_foreign_map_points(fault):
    r, reader, _ = definition_runtime()
    p = r.parts
    if fault == 'domain': r.p64(r.base+0x4d3878, 0)
    if fault == 'other_level':
        r.add_class('GameLevel_Other', 'XDTGameSystem.GameLevels', [], size=16)
        r.p64(p.node+16, r.make_object('GameLevel_Other', 16))
    if fault == 'removed_registry':
        r.write(p.entries+36, struct.pack('<i', -2))
        r.write(p.registry+68, struct.pack('<i', 2))
    if fault == 'nan': r.write(p.rows[0]+28, struct.pack('<f', float('nan')))
    if fault == 'duplicate_id': r.write(p.rows[1]+16, struct.pack('<I', 100))
    if fault == 'point_type':
        r.p64(r.classes['CollectableResourcePointAsset']+0x50, r.string('Unrelated.Data'))
    fields = struct.unpack('<Q', r.read(r.classes['CollectableResourcePointAsset']+0xa0, 8))[0]
    if fault == 'position_type':
        field_type = struct.unpack('<Q', r.read(fields+96, 8))[0]
        r.p64(field_type, r.classes['ResType'])
    if fault == 'item_code':
        field_type = struct.unpack('<Q', r.read(fields+32, 8))[0]
        r.write(field_type+10, b'\x0c')
    if fault == 'position_offset': r.write(fields+96+24, struct.pack('<i', 1000))
    with pytest.raises(ValueError): reader.snapshot()


def test_removing_config_after_a_successful_read_never_returns_cached_old_world_data():
    r, reader, _ = definition_runtime()
    reader.snapshot()
    r.write(r.parts.entries+36, struct.pack('<i', -2))
    r.write(r.parts.registry+68, struct.pack('<i', 2))
    with pytest.raises(ValueError): reader.snapshot()


def test_definition_list_revision_refreshes_cached_points_without_waiting_for_expiry():
    r, reader, _ = definition_runtime()
    old = reader.snapshot()
    r.write(r.parts.rows[0]+28, struct.pack('<f', -650.))
    r.write(r.parts.points+28, struct.pack('<i', 2))
    fresh = reader.snapshot()
    assert old[0]['position'][0] == -500. and fresh[0]['position'][0] == -650.
    assert reader.loads == 2


def mineral(x, category='rock', **extra):
    return {'position': [x, 20., 0.], 'category': category, **extra}


def test_live_markers_replace_definition_and_cached_duplicates_without_mutating_scene():
    live = mineral(10)
    cached = mineral(30, map_cached=True)
    definitions = [mineral(10, map_definition=True), mineral(30, map_definition=True)]
    merged = combine_resource_markers([live, cached], definitions)
    assert len(merged) == 2
    assert next(o for o in merged if o['position'][0] == 10).get('map_definition') is not True
    assert next(o for o in merged if o['position'][0] == 30)['map_definition']
    assert all(o['map_global'] for o in merged)
    assert 'map_global' not in live and 'map_global' not in cached


def test_far_minerals_remain_visible_while_local_objects_keep_their_radius_limit():
    canvas = Mock()
    cache = MapDrawCache(canvas, ranks={'rock': 1})
    source = {'objects': [mineral(200), mineral(150, 'tree')], 'colliders': [],
              'global_resources': [mineral(200, map_definition=True), mineral(-200, 'ore', map_definition=True)]}
    objects, _ = cache.geometry.select(source, [0, 20, 0], 80, Viewport(1000, 600, 0, 0, 1))
    assert {o['position'][0] for o in objects} == {-200, 200}
    assert len(objects) == 2
    assert 'map_global' not in source['objects'][0]


def test_removing_full_map_data_invalidates_retained_symbols_and_old_world_points():
    cache = MapDrawCache(Mock())
    source = {'objects': [], 'colliders': [], 'global_resources': [mineral(200, map_definition=True)]}
    view = Viewport(1000, 600, 0, 0, 1)
    objects, _ = cache.geometry.select(source, [0, 20, 0], 80, view)
    assert len(objects) == 1
    source['global_resources'] = []
    assert not cache.geometry.select(source, [0, 20, 0], 80, view)[0]


@pytest.mark.parametrize('bad', [mineral(50, 'tree'), mineral(float('nan')), {'category': 'rock'},
                               mineral(50, map_definition=False)])
def test_unverified_or_malformed_definitions_are_not_promoted_to_global_markers(bad):
    assert combine_resource_markers([], [bad]) == []


def test_mineral_overview_fits_far_definitions_keeps_player_centered_and_routes_unchanged(tmp_path):
    window = category_window(tmp_path)
    points = [mineral(-600, map_definition=True), mineral(650, 'ore', map_definition=True)]
    window.scene_state['global_resources'] = points
    window.scene_state['global_resource_state'] = 'ready'
    original = [point.copy() for point in window.saved_points]
    window.show_mineral_overview()
    window.draw()
    viewport = window.map_cache.viewport
    assert window.map_view == 'minerals' and window.scene_categories == {'rock', 'ore'}
    assert viewport.screen(window.view.position[0], window.view.position[2]) == (450., 250.)
    for point in points:
        x, y = viewport.screen(point['position'][0], point['position'][2])
        assert 35 <= x <= 865 and 35 <= y <= 465
    assert {'scene-rock', 'scene-ore'} <= window.map_cache.visible_tags
    assert window.saved_points == original
