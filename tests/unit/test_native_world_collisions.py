"""Native streaming collision contracts over a synthetic IL2CPP heap only."""
import math
import struct

import pytest

from utils.core.coordinate_navigation import ObstacleMap, plan_path
from utils.core.scene_map import quaternion, rotate
from utils.drivers.il2cpp_scene import NativeSceneMetadata
from utils.drivers.native_world_collisions import (
    NativeWorldCollisions, _WOOD_RAIL_CENTER, _WOOD_RAIL_SIZE, world_box)


class Heap:
    def __init__(self):
        self.next, self.blocks, self.classes = 0x10000000, {}, {}
        self.assembly, self.types = 0x100000, 0x4000000
        self.cache = self.alloc(14794*8)
        at = self.assembly + NativeSceneMetadata._class_cache_rva
        self.blocks[at] = bytearray(struct.pack('<Q', self.cache))
        self.counter = 200

    def alloc(self, size):
        at = self.next
        self.next += (size+15)//16*16
        self.blocks[at] = bytearray(size)
        return at

    def read(self, at, size):
        for base, raw in self.blocks.items():
            if base <= at and at+size <= base+len(raw):
                return bytes(raw[at-base:at-base+size])
        return None

    def write(self, at, raw):
        for base, block in self.blocks.items():
            if base <= at and at+len(raw) <= base+len(block):
                block[at-base:at-base+len(raw)] = raw
                return
        raise AssertionError(hex(at))

    def q(self, at, value):
        self.write(at, struct.pack('<Q', value))

    def i(self, at, value):
        self.write(at, struct.pack('<i', value))

    def text(self, value):
        at = self.alloc(256)
        self.write(at, value.encode()+b'\0')
        return at

    def cls(self, name, ns='', fields=(), size=40, index=None, value=False, element=None, parent=0):
        at = self.alloc(0x138)
        self.classes[name] = at
        if index is None:
            index = self.counter
            self.counter += 1
        self.q(self.cache+index*8, at)
        self.q(at+0x10, self.text(name))
        self.q(at+0x18, self.text(ns))
        self.q(at+0x20, self.types+index*88)
        self.q(at+0x40, element or at)
        self.q(at+0x58, parent)
        self.i(at+0xf8, size)
        self.write(at+0x132, bytes([2 if value else 0]))
        self.write(at+0x120, struct.pack('<H', len(fields)))
        if fields:
            table = self.alloc(32*len(fields))
            self.q(at+0x80, table)
            for i, f in enumerate(fields):
                key, offset, code, *flags = f
                typ = self.alloc(16)
                self.write(typ+8, struct.pack('<HB', flags[0] if flags else 6, code))
                self.write(table+i*32, struct.pack('<QQQi', self.text(key), typ, at, offset))
        return at

    def obj(self, name, size=None):
        at = self.alloc(size or struct.unpack('<I', self.read(self.classes[name]+0xf8, 4))[0])
        self.q(at, self.classes[name])
        return at

    def refs(self, name, values):
        array_name = name+'[]'
        if array_name not in self.classes:
            self.cls(array_name, size=32, element=self.classes[name])
        at = self.obj(array_name, 32+len(values)*8)
        self.q(at+24, len(values))
        for i, v in enumerate(values):
            self.q(at+32+i*8, v)
        return at

    def dictionary(self, values):
        if 'Entry' not in self.classes:
            entry = self.cls('Entry', fields=[('hashCode', 16, 8), ('next', 20, 8),
                                            ('key', 24, 8), ('value', 32, 28)], value=True, size=40)
            self.cls('Entry[]', size=32, element=entry)
            self.cls('Dictionary`2', 'System.Collections.Generic',
                     [('entries', 24, 29), ('count', 32, 8), ('version', 36, 8)], size=48)
        array = self.obj('Entry[]', 32+len(values)*24)
        self.q(array+24, len(values))
        for i, v in enumerate(values):
            self.write(array+32+i*24, struct.pack('<iii4xQ', i if v else -1, -1, i, v))
        obj = self.obj('Dictionary`2')
        self.q(obj+24, array)
        self.i(obj+32, len(values))
        return obj


def runtime():
    h = Heap()
    h.cls('XDCollider', 'XDT.Physics', [('collider', 16, 18), ('bounds', 32, 17),
          ('m_gameObject', 64, 18), ('m_useLocalTransform', 96, 2),
          ('_isvalid', 177, 2), ('_cachedColliderEnabled', 178, 2), ('_selfEnable', 179, 2)],
          size=184, index=7737)
    h.cls('SceneLoaderRoot', 'SceneLoader', [('instance', 0, 18, 19), ('configs', 32, 29),
          ('worldOrigin', 40, 17), ('layers', 64, 29)], size=80, index=10404)
    h.cls('LoadLayer', fields=[('config', 16, 18), ('loaded', 24, 21), ('loader', 40, 18)], size=48)
    h.cls('SceneLoaderConfig', fields=[('parent', 16, 18), ('data', 24, 18)])
    h.cls('SceneColliderLoader', 'SceneLoader', [('data', 24, 18), ('_colliderPool', 32, 18),
          ('_root', 40, 18)], size=56)
    h.cls('SceneColliderLoaderData', fields=[('allGrids', 32, 29)], size=48)
    h.cls('SceneColliderPool', 'SceneLoader', [('_instBoxCols', 16, 21), ('_root', 32, 18)], size=88)
    h.cls('GridBoxColData', fields=[('gridColliders', 16, 29)], size=24)
    h.cls('LoadRecord', fields=[('obj', 24, 28)], size=32)
    h.cls('InstBoxColData', fields=[('position', 16, 17), ('rotation', 28, 17), ('scale', 44, 17),
          ('size', 56, 17), ('center', 68, 17), ('index', 80, 8), ('isTrigger', 88, 2), ('layerid', 92, 8)], size=96)
    h.cls('List`1', 'System.Collections.Generic', [('_items', 16, 29), ('_size', 24, 8), ('_version', 28, 8)])
    h.root, h.layer, h.config = (h.obj(n) for n in ('SceneLoaderRoot', 'LoadLayer', 'SceneLoaderConfig'))
    h.loader, h.data, h.pool = (h.obj(n) for n in ('SceneColliderLoader', 'SceneColliderLoaderData', 'SceneColliderPool'))
    h.grid, h.record = h.obj('GridBoxColData'), h.obj('LoadRecord')
    h.source, h.live = h.obj('InstBoxColData'), h.obj('XDCollider')
    h.pool_list = h.obj('List`1')
    h.loaded = h.dictionary([h.record, 0])
    h.slot_array = h.refs('XDCollider', [h.live])
    h.q(h.pool_list+16, h.slot_array)
    h.i(h.pool_list+24, 1)
    h.layers = h.refs('LoadLayer', [h.layer])
    h.q(h.root+64, h.layers)
    h.q(h.root+32, h.refs('SceneLoaderConfig', [h.config]))
    # This is a culling origin, not an offset to add to every collider.
    h.write(h.root+40, struct.pack('<3f', 256, 0, 256))
    storage = h.alloc(8)
    h.q(storage, h.root)
    h.q(h.classes['SceneLoaderRoot']+0xb8, storage)
    h.q(h.layer+16, h.config)
    h.q(h.layer+24, h.loaded)
    h.q(h.layer+40, h.loader)
    for at, off, value in ((h.config, 16, 0x20000000), (h.config, 24, h.data),
                           (h.loader, 24, h.data), (h.loader, 32, h.pool),
                           (h.loader, 40, 0x20000000), (h.pool, 16, h.pool_list),
                           (h.pool, 32, 0x20000000), (h.data, 32, h.refs('GridBoxColData', [h.grid])),
                           (h.grid, 16, h.refs('InstBoxColData', [h.source])), (h.record, 24, h.grid)):
        h.q(at+off, value)
    position, rotation, scale = [0, 0, 3], [0, math.sqrt(.5), 0, math.sqrt(.5)], [1, 1, 1]
    h.write(h.source+16, struct.pack('<3f4f3f3f3f', *position, *rotation, *scale,
                                    *_WOOD_RAIL_SIZE, *_WOOD_RAIL_CENTER))
    h.i(h.source+92, 10)
    center = [a+b for a, b in zip(position, rotate(_WOOD_RAIL_CENTER, quaternion(rotation)))]
    h.write(h.live+32, struct.pack('<6f', *center, *(v/2 for v in _WOOD_RAIL_SIZE)))
    h.write(h.live+177, b'\x01\x01\x01')
    return h, NativeWorldCollisions(h.read, h.assembly)


def test_stable_native_root_reads_rotated_roadside_rail_and_reuses_geometry():
    h, reader = runtime()
    boxes, stats = reader.snapshot()
    assert len(boxes) == stats['world_fences'] == 1
    assert boxes[0]['category'] == 'fence'
    xs, zs = zip(*boxes[0]['polygon'])
    assert max(xs)-min(xs) > 2 and max(zs)-min(zs) < .15
    # Raw local bounds have the opposite long axis and must not be projected.
    assert boxes[0] is reader.snapshot()[0][0]
    obstacles = ObstacleMap({'colliders': boxes}, (0, 0, 0), water_areas=())
    assert not obstacles.clear((0, 0), (0, 6))
    path = plan_path((0, 0, 0), (0, 0, 6), obstacles)
    assert len(path) > 1 and any(abs(p[0]) > 1 for p in path)


@pytest.mark.parametrize('kind', ['trigger', 'disabled', 'attached', 'invalid_slot', 'stale_bounds', 'degenerate'])
def test_inactive_triggers_reassigned_or_degenerate_boxes_are_not_obstacles(kind):
    h, reader = runtime()
    if kind == 'trigger': h.write(h.source+88, b'\x01')
    if kind == 'disabled': h.write(h.live+178, b'\0')
    if kind == 'attached': h.q(h.live+16, 0x20000000)
    if kind == 'invalid_slot': h.i(h.source+80, 1)
    if kind == 'stale_bounds': h.write(h.live+32, struct.pack('<f', 10))
    if kind == 'degenerate':
        h.write(h.source+56, b'\0'*4)
        h.write(h.live+44, b'\0'*4)
    assert reader.snapshot()[0] == []


def test_removing_loaded_grid_evicts_cached_world_geometry():
    h, reader = runtime()
    assert reader.snapshot()[0]
    h.q(h.record+24, 0)
    assert reader.snapshot()[0] == [] and reader.geometry_cache == {}


@pytest.mark.parametrize('change', ['config', 'pool_owner', 'foreign_grid', 'root', 'loaded_version', 'pool_version', 'slot'])
def test_scene_owner_or_container_changes_reject_the_whole_native_snapshot(change):
    h, reader = runtime()
    if change == 'config': h.q(h.config+24, h.grid)
    if change == 'pool_owner': h.q(h.pool+32, 0x20000008)
    if change == 'foreign_grid': h.q(h.record+24, h.source)
    read = h.read
    def mutate(at, size):
        raw = read(at, size)
        if at == h.live and size == 184:
            if change == 'root':
                storage = struct.unpack('<Q', read(h.classes['SceneLoaderRoot']+0xb8, 8))[0]
                h.q(storage, 0)
            if change == 'loaded_version': h.i(h.loaded+36, 1)
            if change == 'pool_version': h.i(h.pool_list+28, 1)
            if change == 'slot': h.q(h.slot_array+32, 0)
        return raw
    reader.meta.read = mutate
    with pytest.raises(ValueError):
        reader.snapshot()


def test_unknown_template_of_similar_dimensions_stays_generic_but_still_blocks():
    h, reader = runtime()
    # A nearly identical object is not sufficient evidence for a fence label.
    h.write(h.source+92, struct.pack('<i', 11))
    boxes, stats = reader.snapshot()
    assert len(boxes) == 1 and stats['world_fences'] == 0 and 'category' not in boxes[0]
    assert not ObstacleMap({'colliders': boxes}, (0, 0, 0), water_areas=()).clear((0, 0), (0, 6))


def test_native_dictionary_uses_its_own_value_type_flag_and_free_entry_encoding():
    h, reader = runtime()
    assert reader.meta.dictionary(h.loaded) == [h.record]
    assert reader.meta.cls(h.classes['Entry'])['value_type']
    # Bit zero is a different IL2CPP property and must not make a ref type a struct.
    h.write(h.classes['LoadRecord']+0x132, b'\x01')
    reader.meta.cache.pop(h.classes['LoadRecord'], None)
    assert not reader.meta.obj(h.record)['value_type']


def test_metadata_rejects_foreign_field_ownership_and_changed_class_cache():
    h, reader = runtime()
    table = struct.unpack('<Q', h.read(h.classes['LoadRecord']+0x80, 8))[0]
    h.q(table+16, h.classes['LoadLayer'])
    with pytest.raises(ValueError, match='归属'):
        reader.meta.cls(h.classes['LoadRecord'])
    h.q(h.assembly+NativeSceneMetadata._class_cache_rva, h.source)
    with pytest.raises(ValueError): reader.meta.validate_root()


def test_native_dictionary_mutation_during_copy_is_rejected():
    h, reader = runtime()
    entries = struct.unpack('<Q', h.read(h.loaded+24, 8))[0]
    read = h.read
    def mutate(at, size):
        raw = read(at, size)
        if at == entries+32: h.i(h.loaded+36, 1)
        return raw
    reader.meta.read = mutate
    with pytest.raises(ValueError): reader.meta.dictionary(h.loaded)


def test_nonuniform_scale_and_negative_axis_preserve_the_original_rotation():
    source = {'trigger': 0, 'layer': 0, 'position': [0, 2, 0], 'rotation': [0, .6, 0, .8],
              'scale': [-2, 1, 3], 'size': [1, 1, 2], 'center': [1, 0, 1]}
    center = rotate([-2, 0, 3], source['rotation'])
    live = {'flags': b'\x01\x01\x01', 'attached': False,
            'bounds': [center[0], 2, center[2], 1, .5, 3]}
    geometry = world_box(source, live)
    assert geometry['height'] == pytest.approx([1.5, 2.5])
    assert 'category' not in geometry
