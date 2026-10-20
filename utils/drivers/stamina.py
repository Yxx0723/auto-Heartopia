"""Local-role ECS property access through typed metadata; VM_READ only."""
import struct

from utils.core.stamina import stamina_status
from utils.drivers.mono_scene import SceneMetadata


def component_indices(record, read):
    count, capacity, extra = struct.unpack_from('<iiQ', record, 48)
    if not 0 <= count <= 512 or capacity < 0 or capacity > 512:
        raise ValueError('角色属性索引范围无效')
    keys = list(struct.unpack_from('<8H', record))[:min(count, 8)]
    values = list(struct.unpack_from('<8i', record, 16))[:min(count, 8)]
    if count > 8:
        if capacity < count-8 or capacity & (capacity-1) or extra < 0x10000:
            raise ValueError('角色扩展属性索引无效')
        raw = read(extra, capacity*6)
        keys += list(struct.unpack_from('<'+'H'*(count-8), raw))
        values += list(struct.unpack_from('<'+'i'*(count-8), raw, capacity*2))
    if len(keys) != len(set(keys)) or any(v < 0 for v in values):
        raise ValueError('角色属性索引重复或失效')
    return dict(zip(keys, values))


class NativeGrowList:
    def __init__(self, read, address, stride):
        self.read, self.address, self.stride = read, address, stride
        self.header = read(address, 32)
        self.size, mask, bits, self.count, self.segments, length, capacity = struct.unpack('<4iQ2i', self.header)
        self.mask, self.bits = mask, bits
        if not 1 <= self.size <= 65536 or self.size & (self.size-1) or mask != self.size-1 \
                or bits != self.size.bit_length()-1 or not 0 <= self.count <= 262144 \
                or not 0 <= length <= capacity <= 4096 or length < (self.count+self.size-1)//self.size:
            raise ValueError('角色属性分段容器无效')
        self.descriptors = read(self.segments, length*16) if length else b''

    def at(self, index):
        if not 0 <= index < self.count:
            raise ValueError('角色属性索引已失效')
        pointer, length = struct.unpack_from('<Qi', self.descriptors, (index >> self.bits)*16)
        offset = index & self.mask
        if not offset < length <= self.size:
            raise ValueError('角色属性分段范围无效')
        return pointer+offset*self.stride

    def stable(self):
        return (self.read(self.address, 32) == self.header
                and (not self.descriptors or self.read(self.segments, len(self.descriptors)) == self.descriptors))


class StaminaReader:
    def __init__(self, read, locator):
        self.meta, self.locator = SceneMetadata(read, locator.domain), locator
        self.pool_cache, self.record_hint = None, None

    def expected(self, obj, name, namespace):
        ci = self.meta.obj(obj)
        if (ci['name'], ci['namespace']) != (name, namespace):
            raise ValueError('体力对象类型不符')
        return ci

    def pool_type(self, pool):
        meta = self.meta
        rt = meta.ref(pool, '<ItemType>k__BackingField')
        self.expected(rt, 'RuntimeType', 'System')
        f = meta.field(meta.obj(rt)['address'], '_impl')
        if f['code'] != 17 or f['flags'] & 16:
            raise ValueError('体力类型引用无效')
        typ = meta.pointer(rt+f['offset'])
        if meta.data(typ, 16)[10] not in (17, 18):
            raise ValueError('体力组件类型不符')
        return meta.cls(meta.pointer(typ))

    def native_pool(self, pool, typename):
        meta = self.meta
        self.expected(pool, 'EcsComponentPool`1', 'XD.GameGerm.Ecs')
        typ = self.pool_type(pool)
        if (typ['name'], typ['namespace']) != (typename, 'XDTDataAndProtocol.ComponentsData'):
            raise ValueError('体力组件归属不符')
        store = meta.ref(pool, '_pool')
        self.expected(store, 'EcsDataPool`1', '')
        items = meta.ref(store, '_items')
        self.expected(items, 'EcsFixedGrowList`1', 'XD.GameGerm.Ecs')
        f = meta.field(meta.obj(items)['address'], '_nativeItems')
        if f['code'] != 21 or f['flags'] & 16:
            raise ValueError('体力存储布局不符')
        return NativeGrowList(meta.data, items+f['offset'], typ['size']-16), typ

    def snapshot(self):
        meta = self.meta
        sample = self.locator.snapshot()
        entity = next(t['pointer'] for t in sample['paths'][0]['trace'] if t['field'] == '<entity>k__BackingField')
        components = meta.ref(entity, '_components')
        ugcs = [p for p in meta.dictionary(components, max_items=128)
                if (meta.obj(p)['name'], meta.obj(p)['namespace']) == ('UgcComponent', 'XDTGame.GAS')]
        if len(ugcs) != 1:
            raise ValueError('本地角色属性入口尚未就绪')
        ugc = ugcs[0]
        actor = meta.ref(ugc, '<actor>k__BackingField')
        self.expected(actor, 'UgcEntity', 'XDTGame.GAS')
        if meta.ref(actor, '_renderEntity') != entity:
            raise ValueError('本地角色属性归属不符')
        data_field = meta.field(meta.obj(actor)['address'], '_entityData')
        data_type = meta.cls(data_field['type'])
        if data_field['code'] != 17 or (data_type['name'], data_type['size']) != ('EntityData', 48):
            raise ValueError('本地角色属性标识不符')
        identity = meta.data(actor+data_field['offset'], 32)
        world = meta.ref(entity, '_entityWorld')
        if meta.ref(world, '<main>k__BackingField') != entity:
            raise ValueError('角色不是当前本地角色')
        ecs = meta.ref(world, '_clientEcsWorld')
        unit = meta.ref(ecs, '_ecsWorld')
        self.expected(unit, 'UnitEcsWorld', 'XDGame.Ecs')
        pools_at = meta.ref(unit, 'ComponentPools')
        pools = meta.array_refs(pools_at)
        if len(pools) > 4096:
            raise ValueError('体力组件表过大')
        key = (entity, actor, unit, pools_at, tuple(pools))
        wanted = ('EntityData', 'LocalPlayerComponentData', 'PlayerPropertyData')
        if self.pool_cache is None or self.pool_cache[0] != key:
            found = {}
            for i, p in enumerate(pools):
                if not p:
                    continue
                ci = self.pool_type(p)
                if ci['namespace'] == 'XDTDataAndProtocol.ComponentsData' and ci['name'] in wanted:
                    if ci['name'] in found:
                        raise ValueError('体力组件存在歧义')
                    found[ci['name']] = i
            if set(found) != set(wanted):
                raise ValueError('体力组件尚未加载')
            self.pool_cache, self.record_hint = (key, found), None
        ids = self.pool_cache[1]
        entity_pool, _ = self.native_pool(pools[ids['EntityData']], 'EntityData')
        local_pool, _ = self.native_pool(pools[ids['LocalPlayerComponentData']], 'LocalPlayerComponentData')
        prop_pool, prop_type = self.native_pool(pools[ids['PlayerPropertyData']], 'PlayerPropertyData')
        ef = meta.field(meta.obj(unit)['address'], '_entities')
        if ef['code'] != 21 or ef['flags'] & 16:
            raise ValueError('体力角色表布局不符')
        generic = ef['type']
        arg = meta.pointer(meta.pointer(generic+8)+8)
        record_type = meta.cls(meta.pointer(arg))
        if record_type['name'] != 'EcsEntityData' or record_type['size'] != 112:
            raise ValueError('体力角色记录布局不符')
        records = NativeGrowList(meta.data, unit+ef['offset'], 96)

        def resolve(i):
            at = records.at(i)
            raw = meta.data(at, 96)
            if not struct.unpack_from('<I', raw, 64)[0] or struct.unpack_from('<I', raw, 68)[0]:
                return None
            count = struct.unpack_from('<i', raw, 48)[0]
            # Free ECS slots retain their generation and may carry a negative
            # component count. Only the verified local-player archetype applies.
            if not 0 <= count <= 512 or ids['LocalPlayerComponentData'] not in \
                    struct.unpack_from('<8H', raw)[:min(count, 8)]:
                return None
            indices = component_indices(raw, meta.data)
            if not all(ids[n] in indices for n in wanted):
                return None
            if indices[ids['LocalPlayerComponentData']] >= local_pool.count:
                return None
            data_at = entity_pool.at(indices[ids['EntityData']])
            if meta.data(data_at, 32) != identity:
                return None
            if not struct.unpack_from('<I', raw, 64)[0] or struct.unpack_from('<I', raw, 68)[0]:
                raise ValueError('角色属性已失效')
            return at, raw, prop_pool.at(indices[ids['PlayerPropertyData']]), data_at

        match = resolve(self.record_hint) if self.record_hint is not None else None
        if match is None:
            matches = []
            for i in range(records.count):
                item = resolve(i)
                if item:
                    matches.append((i, item))
            if len(matches) != 1:
                raise ValueError('本地角色体力记录不存在或存在歧义')
            self.record_hint, match = matches[0]
        at, record, prop_at, data_at = match
        raw = meta.data(prop_at, prop_type['size']-16)
        def value(name):
            f = meta.field(prop_type['address'], name)
            if f['code'] != 8 or f['flags'] & 16 or not 16 <= f['offset'] <= prop_type['size']-4:
                raise ValueError('体力字段布局不符')
            return struct.unpack_from('<i', raw, f['offset']-16)[0]
        result = stamina_status(value('CurrStamina'), value('BaseMaxStamina'), value('BoostedMaxValue'))
        current = self.locator.snapshot()
        current_entity = next(t['pointer'] for t in current['paths'][0]['trace'] if t['field'] == '<entity>k__BackingField')
        if (current_entity != entity or meta.data(prop_at, len(raw)) != raw or meta.data(at, 96) != record
                or meta.data(data_at, 32) != identity or meta.data(actor+data_field['offset'], 32) != identity
                or meta.ref(ugc, '<actor>k__BackingField') != actor or meta.ref(actor, '_renderEntity') != entity
                or meta.ref(entity, '_entityWorld') != world or meta.ref(world, '<main>k__BackingField') != entity
                or meta.ref(world, '_clientEcsWorld') != ecs or meta.ref(ecs, '_ecsWorld') != unit
                or meta.ref(unit, 'ComponentPools') != pools_at or meta.array_refs(pools_at) != pools
                or not all(g.stable() for g in (entity_pool, local_pool, prop_pool, records))):
            raise ValueError('角色体力在采样时发生变化')
        return result
