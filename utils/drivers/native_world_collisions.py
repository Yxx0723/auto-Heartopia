"""Read the active native streaming box colliders, including roadside rails.

The version-pinned type cache leads to SceneLoaderRoot.instance. A loaded grid
owns each source box; its index must resolve into that loader's live collider
pool. Bounds and enable flags are checked against the source transform. Heap
scanning and target-side calls are unnecessary.
"""
import math
import struct

from utils.core.scene_map import box_geometry, quaternion, rotate
from utils.drivers.il2cpp_scene import NativeSceneMetadata


# This exact local collision template was verified beside the standing player
# against the roadside wooden rail and its next three rotated segments. Unknown
# thin boxes remain generic obstacles; shape ratios alone never label a fence.
_WOOD_RAIL_CENTER = (-.06352109462022781, .4015892446041107, -.007383823394775391)
_WOOD_RAIL_SIZE = (.14089976251125336, .7863317131996155, 2.095755100250244)


def world_box(source, live):
    """Return exact rotated geometry only when its active pool slot agrees."""
    if source['trigger'] or live['flags'] != b'\x01\x01\x01' or live['attached']:
        return None
    p, q, scale = source['position'], quaternion(source['rotation']), source['scale']
    size, center = source['size'], source['center']
    if not all(math.isfinite(v) for values in (p, scale, size, center, live['bounds']) for v in values):
        raise ValueError('原生碰撞数值无效')
    if any(abs(v) < 1e-6 or abs(v) > 1000 for v in scale):
        raise ValueError('原生碰撞缩放无效')
    scaled_center = [v*s for v, s in zip(center, scale)]
    scaled_size = [abs(v*s) for v, s in zip(size, scale)]
    if any(v <= 1e-6 for v in scaled_size):
        return None  # Degenerate pool boxes have no navigable solid footprint.
    calculated = [v+c for v, c in zip(p, rotate(scaled_center, q))]
    # XDCollider stores the oriented box's centre and *local* half extents,
    # not the world-axis AABB. Projecting its bounds directly loses rotation.
    expected = calculated + [v/2 for v in scaled_size]
    if any(abs(a-b) > .015 for a, b in zip(expected, live['bounds'])):
        return None
    geometry = box_geometry(p, q, scaled_center, scaled_size)
    if source['layer'] == 10 and all(abs(a-b) < 1e-6 for a, b in zip(
            (*center, *size), (*_WOOD_RAIL_CENTER, *_WOOD_RAIL_SIZE))):
        geometry.update(category='fence', source='verified-solid-collider')
    else:
        geometry['source'] = 'verified-native-world-collider'
    return geometry


class NativeWorldCollisions:
    def __init__(self, read, assembly_base):
        self.meta = NativeSceneMetadata(read, assembly_base)
        self.root_class = self.meta.class_at(10404, 'SceneLoaderRoot', 'SceneLoader')['address']
        self.geometry_cache = {}
        self.scene_key = None

    def main_terrain_loaded(self):
        """The fixed terrain edges belong only to the verified startown root."""
        if self.scene_key is None:
            return False
        meta = self.meta
        root = meta.static_ref(self.root_class, 'instance')
        if root != self.scene_key[0]:
            return False
        meta.obj(root, ('SceneLoaderRoot', 'SceneLoader'))
        name = meta.ref(root, 'sceneName')
        return (meta.string(name) == 'startown'
                and meta.static_ref(self.root_class, 'instance') == root
                and meta.ref(root, 'sceneName') == name)

    def vector(self, obj, name, count):
        meta = self.meta
        info = meta.obj(obj)
        field = meta.field(info['address'], name, 17)
        if field['offset'] > info['size'] - count*4:
            raise ValueError('原生碰撞向量范围不符')
        return list(struct.unpack('<' + 'f'*count, meta.data(obj + field['offset'], count*4)))

    def snapshot(self):
        meta = self.meta
        meta.validate_root()
        root = meta.static_ref(self.root_class, 'instance')
        meta.obj(root, ('SceneLoaderRoot', 'SceneLoader'))
        # Streaming/culling origins are not transform offsets. Source boxes
        # already use world positions; each is verified against its live slot.
        origin = self.vector(root, 'worldOrigin', 3)
        if any(not math.isfinite(v) or abs(v) > 100000 for v in origin):
            raise ValueError('原生场景原点正在变化')
        layers_at = meta.ref(root, 'layers')
        layers = meta.array_refs(layers_at, ('LoadLayer', ''))
        configs = set(meta.array_refs(meta.ref(root, 'configs'), ('SceneLoaderConfig', '')))
        if not 0 < len(layers) <= 32:
            raise ValueError('原生场景图层范围无效')
        result, guards, cache, visited = [], [], {}, set()
        stats = {'world_boxes': 0, 'world_fences': 0, 'world_inactive': 0,
                 'world_triggers': 0, 'world_unmatched': 0}
        for layer in layers:
            meta.obj(layer, ('LoadLayer', ''))
            loader = meta.ref(layer, 'loader')
            if not loader:
                continue
            info = meta.obj(loader)
            if (info['name'], info['namespace']) not in (
                    ('SceneColliderLoader', 'SceneLoader'), ('QualityHighColliderLoader', 'SceneLoader')):
                continue
            config, data, pool = meta.ref(layer, 'config'), meta.ref(loader, 'data'), meta.ref(loader, '_colliderPool')
            if config not in configs or meta.ref(config, 'data') != data:
                raise ValueError('原生碰撞图层归属已变化')
            meta.obj(data, ('SceneColliderLoaderData', ''))
            meta.obj(pool, ('SceneColliderPool', 'SceneLoader'))
            parent = meta.ref(config, 'parent')
            if not parent or meta.ref(loader, '_root') != parent or meta.ref(pool, '_root') != parent:
                raise ValueError('原生碰撞池归属不符')
            pool_list, loaded = meta.ref(pool, '_instBoxCols'), meta.ref(layer, 'loaded')
            pool_stamp, loaded_stamp = meta.container_stamp(pool_list), meta.container_stamp(loaded, True)
            slots = meta.list_refs(pool_list)
            grids_at = meta.ref(data, 'allGrids')
            all_grids = set(meta.array_refs(grids_at, ('GridBoxColData', '')))
            if len(slots) > 20000 or len(all_grids) > 20000:
                raise ValueError('原生碰撞集合过大')
            guards.append((layer, loader, config, data, pool, parent, pool_list, pool_stamp,
                           loaded, loaded_stamp, grids_at, slots, all_grids))
            for record in meta.dictionary(loaded):
                meta.obj(record, ('LoadRecord', ''))
                grid = meta.ref(record, 'obj')
                if not grid:  # A loading request has no collider grid yet.
                    continue
                if grid not in all_grids:
                    raise ValueError('原生碰撞网格归属不符')
                meta.obj(grid, ('GridBoxColData', ''))
                boxes_at = meta.ref(grid, 'gridColliders')
                for source_at in meta.array_refs(boxes_at, ('InstBoxColData', '')):
                    if source_at in visited:
                        continue
                    visited.add(source_at)
                    if len(visited) > 20000:
                        raise ValueError('原生碰撞数量过大')
                    ci = meta.obj(source_at, ('InstBoxColData', ''))
                    raw = meta.data(source_at, ci['size'])
                    def values(name, count):
                        offset = meta.field(ci['address'], name, 17)['offset']
                        return list(struct.unpack_from('<'+'f'*count, raw, offset))
                    index = struct.unpack_from('<i', raw, meta.field(ci['address'], 'index', 8)['offset'])[0]
                    trigger = raw[meta.field(ci['address'], 'isTrigger', 2)['offset']]
                    if trigger:
                        stats['world_triggers'] += 1
                        continue
                    if not 0 <= index < len(slots) or not slots[index]:
                        stats['world_unmatched'] += 1
                        continue
                    live_at = slots[index]
                    li = meta.obj(live_at, ('XDCollider', 'XDT.Physics'))
                    live_raw = meta.data(live_at, li['size'])
                    def live_offset(name, code):
                        return meta.field(li['address'], name, code)['offset']
                    flags = bytes(live_raw[live_offset(n, 2)] for n in (
                        '_isvalid', '_cachedColliderEnabled', '_selfEnable'))
                    if flags != b'\x01\x01\x01':
                        stats['world_inactive'] += 1
                        continue
                    attached = (any(struct.unpack_from('<Q', live_raw, live_offset(n, 18))[0]
                                    for n in ('collider', 'm_gameObject'))
                                or live_raw[live_offset('m_useLocalTransform', 2)] != 0)
                    source = {n: values(n, ct) for n, ct in (
                        ('position', 3), ('rotation', 4), ('scale', 3), ('size', 3), ('center', 3))}
                    source.update(trigger=trigger, layer=struct.unpack_from(
                        '<i', raw, meta.field(ci['address'], 'layerid', 8)['offset'])[0])
                    live = {'flags': flags, 'attached': attached, 'bounds': list(struct.unpack_from(
                        '<6f', live_raw, live_offset('bounds', 17)))}
                    key = (data, source_at, live_at, raw, tuple(live['bounds']), flags, attached)
                    geometry = self.geometry_cache.get(key)
                    if geometry is None:
                        geometry = world_box(source, live)
                    if geometry is None:
                        stats['world_unmatched'] += 1
                        continue
                    geometry = {**geometry, 'map_static': True, 'map_id': ('world-box', data, source_at)} \
                        if 'map_id' not in geometry else geometry
                    # Recheck the actual slot, source and flags before publication.
                    if meta.data(source_at, ci['size']) != raw or meta.data(live_at, li['size']) != live_raw:
                        raise ValueError('原生碰撞体正在重新分配')
                    cache[key] = geometry
                    result.append(geometry)
                if meta.ref(record, 'obj') != grid or meta.ref(grid, 'gridColliders') != boxes_at:
                    raise ValueError('原生碰撞网格正在卸载')
        for layer, loader, config, data, pool, parent, pool_list, ps, loaded, ls, grids_at, slots, all_grids in guards:
            if (meta.ref(layer, 'loader') != loader or meta.ref(layer, 'config') != config
                    or meta.ref(config, 'data') != data or meta.ref(config, 'parent') != parent
                    or meta.ref(loader, 'data') != data or meta.ref(loader, '_colliderPool') != pool
                    or meta.ref(loader, '_root') != parent or meta.ref(pool, '_root') != parent
                    or meta.ref(pool, '_instBoxCols') != pool_list or meta.container_stamp(pool_list) != ps
                    or meta.list_refs(pool_list) != slots
                    or meta.ref(layer, 'loaded') != loaded or meta.container_stamp(loaded, True) != ls
                    or meta.ref(data, 'allGrids') != grids_at
                    or set(meta.array_refs(grids_at, ('GridBoxColData', ''))) != all_grids):
                raise ValueError('原生碰撞图层正在切换')
        if (meta.static_ref(self.root_class, 'instance') != root or meta.ref(root, 'layers') != layers_at
                or meta.array_refs(layers_at, ('LoadLayer', '')) != layers
                or self.vector(root, 'worldOrigin', 3) != origin):
            raise ValueError('原生场景正在切换')
        meta.validate_root()
        self.geometry_cache = cache
        self.scene_key = (root, layers_at, tuple((g[2], g[3], g[4]) for g in guards))
        stats['world_boxes'] = len(result)
        stats['world_fences'] = sum(c.get('category') == 'fence' for c in result)
        return result, stats
