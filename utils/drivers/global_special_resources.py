"""Read current dynamic map entities, independently of rendered nearby models."""
import struct

from utils.core.coordinate_route import valid_position
from utils.drivers.global_minerals import GlobalMineralReader


KINDS = {(42001, 401): 'roaming_oak', (42002, 402): 'fluorite'}
RESOURCE_NS = 'XDT.Scene.Shared.World.MapResource'


def decode_marker(resource, dynamic, position, entity_id, generation):
    resource_id, item_type, static_id = struct.unpack('<Iii', resource)
    dynamic_id, = struct.unpack('<i', dynamic)
    category = KINDS.get((static_id, item_type))
    xyz = list(struct.unpack('<3f', position))
    if category is None:
        return None
    if not generation or dynamic_id <= 0 or resource_id & 0x7fffffff != dynamic_id or not valid_position(xyz):
        raise ValueError('全图动态资源身份或位置无效')
    return {'category': category, 'position': xyz, 'resource_static_id': static_id,
            'resource_item_type': item_type, 'resource_id': resource_id,
            'map_global': True, 'map_dynamic': True, 'resource_global_only': True,
            'resource_global_validated': True, 'source': 'global-resource-ecs',
            'map_id': ('global-special', entity_id, generation)}


class GlobalSpecialReader(GlobalMineralReader):
    extra_types = {}
    extra_layouts = {}
    def __init__(self, meta, mono_base):
        world = self.find_initial_class(meta, mono_base, 'XDTBaseService', 'GameWorld', 'XDTGame.Core')
        super().__init__(meta, mono_base, {}, classes=(world, None))
        self.service_class = self.find_class('XDTDataAndProtocol', 'EcsService',
                                            'XDTDataAndProtocol.ProtocolService')
        self.bindings = None

    @staticmethod
    def find_initial_class(meta, base, assembly, name, namespace):
        finder = GlobalMineralReader.__new__(GlobalMineralReader)
        finder.meta, finder.base = meta, base
        return finder.find_class(assembly, name, namespace)

    def context(self):
        m = self.meta
        if m.pointer(self.base+0x4d3878) != m.domain:
            raise ValueError('全图动态资源运行时已切换')
        node = m.static_ref(self.world_class, '_currentLevel')
        self.expected(node, 'LevelNode', 'XDTGame.Core')
        level = m.ref(node, '_instance')
        self.expected(level, 'GameLevel_Main', 'XDTGameSystem.GameLevels')
        provider = m.static_ref(self.service_class, '_serviceProvider')
        self.expected(provider, 'EcsInjectSystem', 'ClientSystem')
        world = m.ref(provider, '_ecsWorld')
        self.expected(world, 'EcsWorld', 'XD.GameGerm.Ecs')
        core = m.ref(world, '_core')
        self.expected(core, 'Core', '')
        owner = m.ref(core, 'Owner')
        self.expected(owner, 'EcsWorldTuple', 'XD.GameGerm.Ecs')
        manager = m.ref(core, 'Context')
        self.expected(manager, 'ClientWorldManager', 'XD.GameGerm.Ecs.Boost.Client')
        if m.ref(owner, 'MainWorld') != world or m.ref(manager, '<World>k__BackingField') != owner:
            raise ValueError('全图资源世界归属不符')
        for name in ('<Inited>k__BackingField', '<HandShaked>k__BackingField'):
            field = self.field(m.obj(manager), name, 2, 1)
            if m.data(manager+field['offset'], 1) != b'\x01':
                raise ValueError('全图资源尚未同步')
        return node, level, world, owner, manager, m.ref(owner, 'ComponentPools')

    def pools(self, context):
        m = self.meta
        pointers = tuple(m.array_refs(context[-1]))
        key = (context, pointers)
        if self.bindings and self.bindings[0] == key:
            return self.bindings[1]
        wanted = {'MapResourcePointComponent': (RESOURCE_NS, 28),
                  'DynamicMapEntityComponent': (RESOURCE_NS, 20),
                  'LocalPositionComponent': ('XD.GameGerm.Ecs.Boost.Components', 28)}
        required = set(wanted)
        wanted.update(self.extra_types)
        result = {}
        for index, pool in enumerate(pointers):
            if not pool:
                continue
            self.expected(pool, 'EcsComponentPool`1', 'XD.GameGerm.Ecs')
            rt = m.ref(pool, '<ItemType>k__BackingField')
            self.expected(rt, 'RuntimeType', 'System')
            field = self.field(m.obj(rt), '_impl', 17, 8)
            typ = m.pointer(rt+field['offset'])
            try:
                ci = m.cls(m.pointer(typ))
            except ValueError:
                continue  # Primitive/generic pools have no direct class at this type slot.
            if ci['name'] not in wanted:
                continue
            if m.data(typ, 16)[10] not in (17, 18):
                raise ValueError('全图资源组件类型无效')
            if (ci['namespace'], ci['size']) != wanted[ci['name']] or ci['name'] in result:
                raise ValueError('全图资源组件归属不符')
            result[ci['name']] = (index, pool, ci)
        if not required <= set(result):
            raise ValueError('全图资源组件尚未加载')
        for name, fields in {
                'MapResourcePointComponent': [('resourceId', 9, 16), ('ItemType', 8, 20), ('StaticId', 8, 24)],
                'DynamicMapEntityComponent': [('Id', 8, 16)],
                'LocalPositionComponent': [('Value', 17, 16)], **self.extra_layouts}.items():
            if name not in result:
                continue
            ci = result[name][2]
            for field_name, code, offset in fields:
                if self.field(ci, field_name, code, 4 if code != 17 else 12)['offset'] != offset:
                    raise ValueError('全图资源组件布局改变')
        position = result['LocalPositionComponent'][2]
        vector = m.cls(m.field(position['address'], 'Value')['type'])
        if (vector['name'], vector['namespace'], vector['size']) != ('Vector3', 'UnityEngine', 28):
            raise ValueError('全图资源坐标类型不符')
        for axis, offset in zip('xyz', (16, 20, 24)):
            if self.field(vector, axis, 12, 4)['offset'] != offset:
                raise ValueError('全图资源坐标布局不符')
        self.bindings = key, result
        return result

    def select_components(self, indices, bindings):
        return list(bindings) if all(binding[0] in indices for binding in bindings.values()) else []

    def decode_values(self, values, index, generation):
        return decode_marker(values['MapResourcePointComponent'], values['DynamicMapEntityComponent'],
                             values['LocalPositionComponent'], index, generation)

    def snapshot(self):
        # Lazy import avoids the SceneMetadata -> reader -> stamina import cycle.
        from utils.drivers.stamina import NativeGrowList, component_indices
        m, context = self.meta, self.context()
        bindings = self.pools(context)
        stores = {}
        for name, (index, pool, ci) in bindings.items():
            store = m.ref(pool, '_pool')
            self.expected(store, 'EcsDataPool`1', '')
            items = m.ref(store, '_items')
            info = self.expected(items, 'EcsFixedGrowList`1', 'XD.GameGerm.Ecs')
            field = self.field(info, '_nativeItems', 21, 32)
            stores[name] = NativeGrowList(m.data, items+field['offset'], ci['size']-16)
        owner = context[3]
        field = self.field(m.obj(owner), '_entities', 21, 32)
        arg = m.pointer(m.pointer(field['type']+8)+8)
        ci = m.cls(m.pointer(arg))
        if (ci['name'], ci['namespace'], ci['size']) != ('EcsEntityData', '', 112):
            raise ValueError('全图实体布局不符')
        entities = NativeGrowList(m.data, owner+field['offset'], 96)
        result = []
        for index in range(entities.count):
            at = entities.at(index)
            raw = m.data(at, 96)
            generation = struct.unpack_from('<I', raw, 64)[0]
            count = struct.unpack_from('<i', raw, 48)[0]
            if not generation or not 0 <= count <= 512 or struct.unpack_from('<I', raw, 88)[0] != 0:
                continue
            indices = component_indices(raw, m.data)
            selected = self.select_components(indices, bindings)
            if not selected:
                continue
            reads = [(stores[name].at(indices[bindings[name][0]]), bindings[name][2]['size']-16)
                     for name in selected]
            data = [m.data(addr, size) for addr, size in reads]
            values = dict(zip(selected, data))
            marker = self.decode_values(values, index, generation)
            if marker is not None:
                if raw != m.data(at, 96) or data != [m.data(addr, size) for addr, size in reads]:
                    raise ValueError('全图动态资源正在更新')
                result.append(marker)
        if not entities.stable() or not all(store.stable() for store in stores.values()) or context != self.context():
            raise ValueError('全图资源世界正在切换')
        return result
