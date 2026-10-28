"""Version-pinned read-only scene metadata; no target-side API calls."""
import math
import hashlib
import struct
import time
from utils.core import thresholds as policy
from utils.core.scene_read import SceneReadIssue, RecoveryBudget, read_collection
from utils.core.scene_map import box_geometry, compose_transform
from utils.core.terrain_map import verified_terrain_walls
from utils.core.navigation_reference import default_reference_path, load_reference
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.native_world_collisions import NativeWorldCollisions
from utils.drivers.global_minerals import GlobalMineralReader
from utils.drivers.house_entrances import HouseEntranceReader
from utils.drivers.special_gather import SPECIAL_COMPONENTS, special_gather

# Verified on 2026-10-14 against the standing player's blueberry bush and the
# user's identified raspberry and ordinary bushes (each within 0.2 world units).
# The coordinate profile pins the game build.
BUSH_ITEM_TYPES = {101: 'bush', 102: 'blueberry', 103: 'raspberry'}
# Verified on 2026-10-20 at the user's identified rock (0.539 units) and ore
# (0.407 units), using exact gather components and their own _itemTypeID fields.
# Unknown stone IDs stay generic until their actual resource identity is checked.
STONE_ITEM_TYPES = {301: 'rock', 302: 'ore'}
# Both exact types are present in the inspected game's vegetation metadata.
TREE_COMPONENTS = {('CollectableTreeComponent', 'XDTLevelAndEntity.Gameplay.Component.Gather'),
                   ('CollectableAdvancedTreeComponent', 'XDTLevelAndEntity.Gameplay.Component.Gather')}
FAUNA_COMPONENTS = {
    ('InsectComponent', 'XDTLevelAndEntity.Gameplay.Component.Insect'): 'insect',
    ('BirdComponent', 'ScriptsRefactory.LevelAndEntity.Entity.Components'): 'bird',
    ('AnimalMovementComponent', 'XDTLevelAndEntity.Gameplay.Component.Animal'): 'animal',
}
# Exact live processor identities inspected on 2026-10-15. Their own collider
# target lists establish ownership; shape, names and static ID ranges do not.
_BAKE_NAMESPACE = 'XDTLevelAndEntity.BaseSystem.RenderingManager.BakeRendering'
_BARRIER_PROCESSORS = {
    ('BakeRenderingProcessorProcessorForFence', _BAKE_NAMESPACE): 'fence',
    ('BakeRenderingProcessorProcessorForGridFence', _BAKE_NAMESPACE): 'fence',
    ('BakeRenderingProcessorProcessorForStairHandrail', _BAKE_NAMESPACE): 'handrail',
}
_RENDER_TARGET = ('RenderPhysicTarget', _BAKE_NAMESPACE + '.Collider')


class SceneMetadata:
    def __init__(self, read, domain):
        self.read, self.domain, self.cache = read, domain, {}
        self.stage = 'metadata'
        self.read_counts = None
        self.batch_object_headers = False

    def data(self, address, size):
        if not 0x10000 <= address < 0x7fffffffffff or not 0 < size <= policy.COORD_SCENE_MAX_BYTES:
            raise ValueError('场景数据范围无效')
        if self.read_counts is not None:
            self.read_counts[self.stage] = self.read_counts.get(self.stage, 0) + 1
        value = self.read(address, size)
        if value is None or len(value) != size:
            raise ValueError('场景数据正在变化')
        return value

    def pointer(self, address):
        value = struct.unpack('<Q', self.data(address, 8))[0]
        if value and not 0x10000 <= value < 0x7fffffffffff:
            raise SceneReadIssue('场景引用无效', kind='invalid_reference', stage=self.stage)
        return value

    def integer(self, address):
        return struct.unpack('<i', self.data(address, 4))[0]

    def text(self, address):
        raw = self.data(address, policy.MONO_NAME_BYTES)
        if b'\0' not in raw:
            raise ValueError('场景类型名称无效')
        return raw.split(b'\0', 1)[0].decode('utf-8')

    def managed_string(self, address):
        if not address:
            return ''
        info = self.obj(address)
        if (info['name'], info['namespace']) != ('String', 'System'):
            raise ValueError('场景名称类型不符')
        length = self.integer(address + 16)
        if not 0 <= length <= policy.COORD_SCENE_LABEL_MAX_CHARS:
            raise ValueError('场景名称长度无效')
        value = self.data(address + 20, length * 2).decode('utf-16le') if length else ''
        if self.integer(address + 16) != length or self.obj(address)['address'] != info['address']:
            raise ValueError('场景名称正在变化')
        return value

    def cls(self, address):
        if address in self.cache:
            return self.cache[address]
        raw = self.data(address, 0x110)
        kind, size = raw[0x1b], struct.unpack_from('<I', raw, 0x1c)[0]
        if kind not in (1, 2, 3, 4, 5, 6) or not 16 <= size <= policy.MONO_OBJECT_LIMIT:
            raise ValueError('场景类型布局不支持')
        count = struct.unpack_from('<I', raw, 0x100)[0] if kind in (1, 2) else 0
        if kind == 3:
            container = self.pointer(self.pointer(address + 0xf0))
            count = self.integer(container + 0x100)
        if not 0 <= count <= policy.MONO_FIELD_LIMIT:
            raise ValueError('场景字段数量无效')
        fields = {}
        if count:
            table = self.pointer(address + 0xa0)
            for i in range(count):
                typ, name, owner, offset = struct.unpack('<QQQi', self.data(table + i * 32, 28))
                details = self.data(typ, 16)
                fields[self.text(name)] = {'offset': offset, 'code': details[10],
                    'flags': struct.unpack_from('<H', details, 8)[0], 'type': struct.unpack_from('<Q', details)[0]}
        result = {'address': address, 'name': self.text(self.pointer(address + 0x48)),
                  'namespace': self.text(self.pointer(address + 0x50)), 'size': size, 'kind': kind,
                  'parent': self.pointer(address + 0x30), 'element': self.pointer(address), 'fields': fields}
        self.cache[address] = result
        return result

    def obj(self, address):
        vtable = self.pointer(address)
        if not self.batch_object_headers:
            if self.pointer(vtable + 16) != self.domain:
                raise ValueError('场景对象归属已变化')
            return self.cls(self.pointer(vtable))
        # Read the same type/domain witness together, every time. Never cache
        # object identity or mutable payload across queries or scans.
        raw = self.data(vtable, 24)
        klass, domain = struct.unpack_from('<Q', raw)[0], struct.unpack_from('<Q', raw, 16)[0]
        if domain and not 0x10000 <= domain < 0x7fffffffffff:
            raise SceneReadIssue('场景引用无效', kind='invalid_reference', stage=self.stage)
        if domain != self.domain:
            raise ValueError('场景对象归属已变化')
        if klass and not 0x10000 <= klass < 0x7fffffffffff:
            raise SceneReadIssue('场景引用无效', kind='invalid_reference', stage=self.stage)
        return self.cls(klass)

    def field(self, klass, name):
        seen = set()
        while klass and klass not in seen and len(seen) < policy.MONO_PARENT_LIMIT:
            seen.add(klass)
            info = self.cls(klass)
            if name in info['fields']:
                field = info['fields'][name]
                if field['offset'] < 0 or field['offset'] >= policy.MONO_OBJECT_LIMIT:
                    raise ValueError('场景字段偏移无效')
                return field
            klass = info['parent']
        raise ValueError('场景对象缺少字段：' + name)

    def ref(self, obj, name):
        field = self.field(self.obj(obj)['address'], name)
        if field['code'] not in (18, 21, 28, 29) or field['flags'] & 16:
            raise ValueError('场景引用字段类型不符')
        return self.pointer(obj + field['offset'])

    def static_ref(self, klass, name):
        field = self.field(klass, name)
        if not field['flags'] & 16:
            raise ValueError('场景静态入口类型不符')
        vtable = self.pointer(klass + 0xd8)
        if self.pointer(vtable) != klass or self.pointer(vtable + 16) != self.domain:
            raise ValueError('场景静态入口已变化')
        slots = self.integer(klass + 0x5c)
        if not 0 <= slots < policy.MONO_VTABLE_LIMIT:
            raise ValueError('场景静态入口范围无效')
        storage = self.pointer(vtable + 0x50 + slots * 8)
        return self.pointer(storage + field['offset'])

    def array(self, address, max_items=policy.COORD_SCENE_MAX_ITEMS):
        info = self.obj(address)
        if info['kind'] not in (4, 5):
            raise ValueError('场景集合类型不符')
        length = struct.unpack('<Q', self.data(address + 24, 8))[0]
        if not 0 <= max_items <= policy.COORD_SCENE_MAX_CAPACITY or length > max_items:
            raise ValueError('场景集合过大')
        return info, length

    def array_refs(self, address):
        info, length = self.array(address)
        return list(struct.unpack('<' + 'Q' * length, self.data(address + 32, length * 8))) if length else []

    def dictionary(self, obj, max_items=policy.COORD_SCENE_MAX_ITEMS):
        info = self.obj(obj)
        if (info['name'], info['namespace']) != ('Dictionary`2', 'System.Collections.Generic'):
            raise ValueError('场景集合类型不符')
        klass = info['address']
        fields = [self.field(klass, name) for name in ('_version', '_count', '_freeCount')]
        if any(f['code'] != 8 or f['flags'] & 16 or not 16 <= f['offset'] <= info['size']-4 for f in fields):
            raise ValueError('场景集合计数字段无效')
        addresses = [obj+f['offset'] for f in fields]
        def stamp():
            return (*[self.integer(at) for at in addresses], self.ref(obj, '_entries'))
        before = stamp()
        _, count, free, entries = before
        # .NET _count is the occupied high-water mark, not the number of live
        # objects. A long-lived registry can have a large array and many holes.
        if not 0 <= free <= count <= policy.COORD_SCENE_MAX_CAPACITY or not 0 <= count-free <= max_items:
            raise ValueError('场景集合数量无效')
        if count == free:
            if stamp() != before:
                raise SceneReadIssue('场景集合在读取时发生变化', kind='mutation', stage=self.stage, retryable=True)
            return []
        array, length = self.array(entries, policy.COORD_SCENE_MAX_CAPACITY)
        element = self.cls(array['element'])
        stride = element['size'] - 16
        if count > length or not 16 <= stride <= policy.MONO_OBJECT_LIMIT:
            raise ValueError('场景集合布局无效')
        nxt = self.field(element['address'], 'next')['offset'] - 16
        value = self.field(element['address'], 'value')['offset'] - 16
        if not 0 <= nxt <= stride - 4 or not 0 <= value <= stride - 8:
            raise ValueError('场景集合字段范围无效')
        raw = self.data(entries + 32, count * stride)
        result, occupied = [], 0
        for i in range(count):
            if struct.unpack_from('<i', raw, i * stride + nxt)[0] >= -1:
                occupied += 1
                address = struct.unpack_from('<Q', raw, i * stride + value)[0]
                if address:
                    result.append(address)
        if occupied != count-free or stamp() != before:
            raise SceneReadIssue('场景集合在读取时发生变化', kind='mutation', stage=self.stage, retryable=True)
        return result

    def list_refs(self, obj):
        """Read only the occupied reference elements, with mutation checks."""
        info = self.obj(obj)
        if (info['name'], info['namespace']) != ('List`1', 'System.Collections.Generic'):
            raise ValueError('场景列表类型不符')
        fields = [self.field(info['address'], name) for name in ('_size', '_version')]
        if any(f['code'] != 8 or f['flags'] & 16 or not 16 <= f['offset'] <= info['size'] - 4 for f in fields):
            raise ValueError('场景列表布局不符')
        size_at, version_at = (obj + f['offset'] for f in fields)
        version, size = self.integer(version_at), self.integer(size_at)
        if not 0 <= size <= policy.COORD_SCENE_MAX_ITEMS:
            raise ValueError('场景列表过大')
        array = self.ref(obj, '_items')
        if not size:
            result = []
        else:
            array_info, capacity = self.array(array, policy.COORD_SCENE_MAX_CAPACITY)
            element = self.cls(array_info['element'])
            parent = self.cls(element['parent']) if element['parent'] else None
            if size > capacity or element['kind'] not in (1, 2, 3) \
                    or parent and (parent['name'], parent['namespace']) in {('ValueType', 'System'), ('Enum', 'System')}:
                raise ValueError('场景列表不是对象引用')
            result = list(struct.unpack('<' + 'Q' * size, self.data(array + 32, size * 8)))
        if self.integer(version_at) != version or self.integer(size_at) != size or self.ref(obj, '_items') != array:
            raise ValueError('场景列表在读取时发生变化')
        return result

    def image_class(self, source_class, name, namespace):
        """Find a class in the already validated player's image, once per reader."""
        image = self.pointer(source_class + 0x40)
        key = (image, name, namespace)
        found_cache = getattr(self, '_image_classes', {})
        if key in found_cache:
            return found_cache[key]
        size, count, buckets = struct.unpack_from('<IIQ', self.data(image + 0x4d0, 40), 24)
        if not 0 < size <= policy.MONO_CLASS_LIMIT or not 0 < count <= policy.MONO_CLASS_LIMIT:
            raise ValueError('场景类型缓存范围无效')
        visited, found = set(), []
        for klass in struct.unpack('<' + 'Q' * size, self.data(buckets, size * 8)):
            while klass:
                if klass in visited or len(visited) >= count:
                    raise ValueError('场景类型缓存发生变化')
                visited.add(klass)
                raw = self.data(klass, 0x110)
                if raw[0x1b] not in (1, 2) or struct.unpack_from('<Q', raw, 0x40)[0] != image:
                    raise ValueError('场景类型缓存归属不符')
                if self.text(struct.unpack_from('<Q', raw, 0x48)[0]) == name \
                        and self.text(struct.unpack_from('<Q', raw, 0x50)[0]) == namespace:
                    found.append(klass)
                klass = struct.unpack_from('<Q', raw, 0x108)[0]
        if len(found) != 1:
            raise ValueError('场景渲染类型未加载或存在歧义')
        self._image_classes = found_cache
        found_cache[key] = found[0]
        return found[0]


class MemorySceneMap:
    @staticmethod
    def local_hierarchy(sample):
        matches = [link['pointer'] for link in sample['paths'][0]['trace'] if link['field'] == '_hierarchy']
        if len(matches) != 1:
            raise ValueError('本地场景层级入口不完整')
        return matches[0]

    def __init__(self, pid, profile):
        self.memory = ReadOnlyPosition(pid, profile)
        if self.memory.locator is None:
            self.memory.close()
            raise ValueError('当前游戏版本不支持场景地图')
        self.meta = SceneMetadata(self.memory._read, self.memory.locator.domain)
        self.native = None
        self.global_minerals = None
        # Optional local source geometry is parsed once on this background
        # reader's opening, not for each UI refresh / steering edge. Each
        # publication below still requires a freshly verified main-map gate.
        self.navigation_reference = load_reference(default_reference_path(),
            self.memory.profile.get('game_assembly_sha256'), True)

    def close(self):
        self.memory.close()

    def barrier_owners(self):
        """Associate exact fence/handrail processors with their solid colliders.

        Optional semantic enrichment. An unavailable rendering registry must
        never erase the ordinary live collision geometry used by navigation.
        """
        meta = self.meta
        list_versions = []
        def refs(obj):
            at = obj + meta.field(meta.obj(obj)['address'], '_version')['offset']
            version = meta.integer(at)
            result = meta.list_refs(obj)
            if meta.integer(at) != version:
                raise ValueError('围栏列表发生变化')
            list_versions.append((at, version))
            return result
        klass = meta.image_class(self.memory.locator.character_class, 'RenderingSystem',
                                 'XDTLevelAndEntity.BaseSystem.RenderingManager')
        root = meta.static_ref(klass, '<Instance>k__BackingField')
        if meta.obj(root)['address'] != klass:
            raise ValueError('场景渲染入口归属不符')
        loader = meta.ref(root, '<LoaderManager>k__BackingField')
        li = meta.obj(loader)
        if (li['name'], li['namespace']) != ('LoaderManager', 'XDTLevelAndEntity.BaseSystem.LoadManager'):
            raise ValueError('场景加载入口类型不符')
        collections_ref = meta.ref(loader, '_bakeCollections')
        collections = refs(collections_ref)
        owners, checks = {}, []
        for collection in collections:
            ci = meta.obj(collection)
            if (ci['name'], ci['namespace']) not in {('NetBakeCollection', _BAKE_NAMESPACE),
                                                     ('LocalBakeCollection', _BAKE_NAMESPACE)}:
                continue
            processors_ref = meta.ref(collection, '_gridFusionBakes')
            processors = refs(processors_ref)
            for processor in processors:
                pi = meta.obj(processor)
                category = _BARRIER_PROCESSORS.get((pi['name'], pi['namespace']))
                if not category:
                    continue
                if meta.ref(processor, 'bakeCollection') != collection:
                    raise ValueError('围栏场景归属发生变化')
                targets_ref = meta.ref(processor, '_colliderObjs')
                targets = refs(targets_ref)
                for target in targets:
                    ti = meta.obj(target)
                    if (ti['name'], ti['namespace']) != _RENDER_TARGET:
                        raise ValueError('围栏碰撞体归属不符')
                    hierarchy = meta.ref(target, '_hierarchy')
                    dictionary = meta.ref(target, '_physicObjects')
                    di = meta.obj(dictionary)
                    if (di['name'], di['namespace']) != ('Dictionary`2', 'System.Collections.Generic'):
                        raise ValueError('围栏碰撞集合类型不符')
                    version_at = dictionary + meta.field(di['address'], '_version')['offset']
                    version = meta.integer(version_at)
                    colliders = frozenset(meta.dictionary(dictionary))
                    if target in owners and owners[target]['category'] != category:
                        raise ValueError('围栏碰撞归属存在歧义')
                    owners[target] = {'category': category, 'hierarchy': hierarchy, 'colliders': colliders,
                                      'dictionary': dictionary, 'version_at': version_at, 'version': version,
                                      'list_version_at': list_versions[-1][0], 'list_version': list_versions[-1][1]}
                    checks.append((target, hierarchy, dictionary, version_at, version))
                if meta.ref(processor, '_colliderObjs') != targets_ref or meta.list_refs(targets_ref) != targets \
                        or meta.ref(processor, 'bakeCollection') != collection:
                    raise ValueError('围栏列表发生变化')
            if meta.ref(collection, '_gridFusionBakes') != processors_ref or meta.list_refs(processors_ref) != processors:
                raise ValueError('围栏渲染列表发生变化')
        for target, hierarchy, dictionary, version_at, version in checks:
            if meta.ref(target, '_hierarchy') != hierarchy or meta.ref(target, '_physicObjects') != dictionary \
                    or meta.integer(version_at) != version:
                raise ValueError('围栏碰撞集合发生变化')
        if meta.static_ref(klass, '<Instance>k__BackingField') != root \
                or meta.ref(root, '<LoaderManager>k__BackingField') != loader \
                or meta.ref(loader, '_bakeCollections') != collections_ref or meta.list_refs(collections_ref) != collections:
            raise ValueError('围栏场景已切换')
        if any(meta.integer(at) != version for at, version in list_versions):
            raise ValueError('围栏渲染列表发生变化')
        return owners

    def collider_barrier(self, collider, owner, hierarchy, barriers):
        """A category is usable only while its actual ownership still holds."""
        barrier = barriers.get(owner)
        meta = self.meta
        try:
            if barrier and collider in barrier['colliders'] and hierarchy == barrier['hierarchy'] \
                    and meta.ref(owner, '_hierarchy') == hierarchy \
                    and meta.ref(owner, '_physicObjects') == barrier['dictionary'] \
                    and meta.integer(barrier['version_at']) == barrier['version'] \
                    and meta.integer(barrier['list_version_at']) == barrier['list_version']:
                return barrier['category']
        except ValueError:
            pass  # Optional label failure must not discard a valid solid box.
        return None

    def insect_category(self, component, info, owner):
        """Use the insect's own configuration name, never its flight pattern or ID prefix."""
        meta = self.meta
        try:
            if meta.ref(component, '_levelEntityComponent') != owner:
                return 'other'
            table = meta.ref(component, '_tableInsect')
            table_info = meta.obj(table)
            if (table_info['name'], table_info['namespace']) != ('TableInsect', ''):
                return 'insect'
            data_field = meta.field(info['address'], '_componentData')
            if data_field['code'] != 17 or data_field['flags'] & 16:
                return 'insect'
            data_info = meta.cls(data_field['type'])
            if (data_info['name'], data_info['namespace']) != ('InsectComponentData', 'XDTDataAndProtocol.ComponentsData'):
                return 'insect'
            if not 16 <= data_field['offset'] <= info['size'] - (data_info['size'] - 16):
                return 'insect'
            table_id_field = meta.field(table_info['address'], 'id')
            static_id_field = meta.field(data_info['address'], 'staticId')
            name_field = meta.field(table_info['address'], 'name')
            for field, code, size, width in [(table_id_field, 8, table_info['size'], 4),
                                             (static_id_field, 8, data_info['size'], 4),
                                             (name_field, 14, table_info['size'], 8)]:
                if field['code'] != code or field['flags'] & 16 or not 16 <= field['offset'] <= size - width:
                    return 'insect'
            table_id_at = table + table_id_field['offset']
            static_id_at = component + data_field['offset'] - 16 + static_id_field['offset']
            item_id = meta.integer(table_id_at)
            if item_id <= 0 or meta.integer(static_id_at) != item_id:
                return 'insect'
            name_at = table + name_field['offset']
            name_ref = meta.pointer(name_at)
            name = meta.managed_string(name_ref).strip()
            if (meta.ref(component, '_tableInsect') != table or meta.ref(component, '_levelEntityComponent') != owner
                    or meta.integer(table_id_at) != item_id or meta.integer(static_id_at) != item_id
                    or meta.pointer(name_at) != name_ref):
                return 'other'
            return 'butterfly' if name.endswith('蝶') or name.casefold().endswith('butterfly') else 'insect'
        except ValueError:
            return 'insect'

    def object_category(self, owner, info):
        """Identify resources and fauna from components belonging to this exact entity."""
        if (info['name'], info['namespace']) != ('LevelEntityComponent', 'XDTLevelAndEntity.EntityView'):
            return 'other'
        meta = self.meta
        try:
            entity = meta.ref(owner, '<entity>k__BackingField')
            entity_info = meta.obj(entity)
            if (entity_info['name'], entity_info['namespace']) != ('Entity', 'XDTGame.GAS.Client'):
                return 'other'
            components = meta.ref(entity, '_components')
            container = meta.obj(components)
            if (container['name'], container['namespace']) != ('Dictionary`2', 'System.Collections.Generic'):
                return 'other'
            for name in ('_count', '_version'):
                field = meta.field(container['address'], name)
                if field['code'] != 8 or field['flags'] & 16:
                    return 'other'
            version_at = components + meta.field(container['address'], '_version')['offset']
            before = meta.integer(version_at)
            categories, resource_reads, identity_reads = set(), [], []
            for component in meta.dictionary(components, max_items=policy.COORD_SCENE_ENTITY_COMPONENT_LIMIT):
                component_info = meta.obj(component)
                special = (component_info['name'], component_info['namespace']) in SPECIAL_COMPONENTS
                if special:
                    try:
                        categories.add(special_gather(meta, component, entity)['category'])
                    except ValueError:
                        categories.add('other')
                    identity_reads.append(component)
                elif (component_info['name'], component_info['namespace']) in TREE_COMPONENTS:
                    if meta.ref(component, '<entity>k__BackingField') != entity:
                        return 'other'
                    categories.add('tree')
                    identity_reads.append(component)
                fauna = FAUNA_COMPONENTS.get((component_info['name'], component_info['namespace']))
                if fauna:
                    if meta.ref(component, '<entity>k__BackingField') != entity:
                        return 'other'
                    categories.add(self.insect_category(component, component_info, owner) if fauna == 'insect' else fauna)
                    identity_reads.append(component)
                gather = {('CollectableBushComponent', 'XDTLevelAndEntity.Gameplay.Component.Gather'):
                              (BUSH_ITEM_TYPES, 'shrub'),
                          ('CollectableStoneComponent', 'XDTLevelAndEntity.Gameplay.Component.Gather'):
                              (STONE_ITEM_TYPES, 'other')}.get((component_info['name'], component_info['namespace']))
                if gather:
                    item_types, fallback = gather
                    if meta.ref(component, '<entity>k__BackingField') != entity:
                        return 'other'
                    try:
                        item_field = meta.field(component_info['address'], '_itemTypeID')
                        if (item_field['code'] != 8 or item_field['flags'] & 16
                                or not 16 <= item_field['offset'] <= component_info['size'] - 4):
                            raise ValueError('Unsupported gather item identity')
                        item_at = component + item_field['offset']
                        item_id = meta.integer(item_at)
                        categories.add(item_types.get(item_id, fallback))
                        resource_reads.append((component, item_at, item_id))
                    except ValueError:
                        categories.add(fallback)
            if (meta.integer(version_at) != before or meta.ref(entity, '_components') != components
                    or meta.ref(owner, '<entity>k__BackingField') != entity):
                return 'other'
            for component, item_at, item_id in resource_reads:
                if meta.integer(item_at) != item_id or meta.ref(component, '<entity>k__BackingField') != entity:
                    return 'other'
            if any(meta.ref(component, '<entity>k__BackingField') != entity for component in identity_reads):
                return 'other'
            if len(categories) == 1:
                return categories.pop()
            if categories & {'tree', 'rock', 'ore', 'fluorite', 'roaming_oak', 'insect', 'butterfly', 'bird', 'animal', 'other'}:
                return 'other'
            return 'shrub' if categories else 'other'
        except ValueError:
            # An unloading entity or incompatible component layout must not hide the scene.
            return 'other'

    def group_hierarchy(self, owner, owners):
        if owner in owners:
            return owners[owner]
        if self.meta.obj(owner)['name'] == 'PhysicalCollisionGroup':
            entity = self.meta.ref(owner, '<entity>k__BackingField')
            if self.meta.obj(entity)['name'] != 'Entity':
                raise ValueError('碰撞体实体归属不符')
            transform = self.meta.ref(entity, '<transformComponent>k__BackingField')
            return self.meta.ref(transform, '_hierarchy')
        return self.meta.ref(owner, '_hierarchy')

    def refresh_live_objects(self, observations, registry, world, *, active=None, stats=None, check=lambda: None):
        """Late recheck after collision reads; never retain a vanished moving icon."""
        meta = self.meta
        active = set(meta.dictionary(registry) if active is None else active)
        result = []
        for hierarchy, owner, item in observations:
            check()
            if hierarchy not in active:
                continue
            if item['category'] in ('insect', 'butterfly', 'bird', 'animal', 'fluorite', 'roaming_oak'):
                try:
                    if meta.ref(hierarchy, '_hierarchyObject') != owner:
                        continue
                    if self.object_category(owner, meta.obj(owner)) != item['category']:
                        continue
                    item = {**item, 'position': world(hierarchy)[0]}
                except (OSError, ValueError):
                    if stats is not None:
                        stats['unknown'] += 1
                    continue
            result.append(item)
        return result

    def read_registry(self, registry, hierarchy_class, local_hierarchy, budget):
        meta = self.meta
        def guard():
            current = self.memory.snapshot()
            if self.local_hierarchy(current) != local_hierarchy or meta.static_ref(hierarchy_class, 'ActiveHierarchies') != registry:
                raise SceneReadIssue('场景已切换，正在重新读取地图', kind='scope_changed', stage=meta.stage)
        return read_collection(lambda: meta.static_ref(hierarchy_class, 'ActiveHierarchies'),
                               meta.dictionary, guard, budget, stage=meta.stage)

    def snapshot(self):
        self.read_budget = RecoveryBudget(cancelled=getattr(self, 'cancelled', lambda: False))
        self.phase_times, self.phase_name, self.phase_started = {}, 'registry', time.monotonic()
        self.meta.read_counts = {} if getattr(self, 'navigation_only', False) else None
        try:
            return self._snapshot()
        finally:
            self._phase(None)
            self.last_read_stats = {'retries': self.read_budget.retries,
                                    'extra_seconds': self.read_budget.spent,
                                    'phases': dict(self.phase_times),
                                    'managed_remote_reads': self.meta.read_counts}

    def _phase(self, name):
        now = time.monotonic()
        if self.phase_name is not None:
            self.phase_times[self.phase_name] = self.phase_times.get(self.phase_name, 0.) + max(0., now-self.phase_started)
        self.phase_name, self.phase_started = name, now

    def _snapshot(self):
        scan_started = time.monotonic()
        self.read_budget.check()
        sample = self.memory.snapshot()
        local_hierarchy = self.local_hierarchy(sample)
        meta = self.meta
        hierarchy_class = meta.obj(local_hierarchy)['address']
        registry = meta.static_ref(hierarchy_class, 'ActiveHierarchies')
        meta.stage = 'registry_start'
        entries = self.read_registry(registry, hierarchy_class, local_hierarchy, self.read_budget)
        transforms, owners, skipped = {}, {}, 0

        def world(address, seen=()):
            if address in transforms:
                return transforms[address]
            if address in seen or len(seen) >= policy.MONO_PARENT_LIMIT:
                raise ValueError('场景层级存在循环')
            info = meta.obj(address)
            if (info['name'], info['namespace']) != ('Hierarchy', 'XDTGame.Core'):
                raise ValueError('场景层级类型不符')
            def values(name, count):
                field = meta.field(info['address'], name)
                return list(struct.unpack('<' + 'f' * count, meta.data(address + field['offset'], count * 4)))
            local = (values('_position', 3), values('_rotation', 4), values('__scale', 1)[0])
            parent = meta.ref(address, '_parent')
            result = compose_transform(world(parent, (*seen, address)), local) if parent else compose_transform(
                ([0, 0, 0], [0, 0, 0, 1], 1.), local)
            transforms[address] = result
            return result

        meta.stage = 'entities'
        self._phase('entities')
        objects, groups, models, observations = [], [], [], []
        discovery_unknown = 0
        for hierarchy in entries:
            self.read_budget.check()
            discovered = False
            try:
                owner = meta.ref(hierarchy, '_hierarchyObject')
                if not owner:
                    continue
                info = meta.obj(owner)
                discovered = True
                owners[owner] = hierarchy
                if getattr(self, 'navigation_only', False) and info['name'] != 'PhysicGroup':
                    # Discover every owner first; only resource classification
                    # and display transforms are omitted, never collider streams.
                    continue
                if info['name'] == 'PhysicGroup':
                    groups.append(owner)
                elif info['name'] not in ('StaticLevelObjectWrap', 'LevelEntityComponent') and hierarchy != local_hierarchy:
                    # A hierarchy anchor is a display landmark, not proof of a
                    # building mesh, solid collider, or traversability.
                    position = world(hierarchy)[0]
                    if math.hypot(position[0]-sample['position'][0], position[2]-sample['position'][2]) <= policy.COORD_MAP_DETAIL_RADIUS:
                        models.append({'position': position, 'model_type': info['name'],
                                       'source': 'loaded-hierarchy-anchor'})
                if info['name'] in ('StaticLevelObjectWrap', 'LevelEntityComponent') and hierarchy != local_hierarchy:
                    obj = {'position': world(hierarchy)[0],
                           'kind': 'entity' if info['name'] == 'LevelEntityComponent' else 'object',
                           'model_type': info['name'],
                           'category': self.object_category(owner, info)}
                    if obj['category'] in ('fluorite', 'roaming_oak'):
                        entity = meta.ref(owner, '<entity>k__BackingField')
                        matches = []
                        for component in meta.dictionary(meta.ref(entity, '_components'), max_items=128):
                            ci = meta.obj(component)
                            if (ci['name'], ci['namespace']) in SPECIAL_COMPONENTS:
                                matches.append(special_gather(meta, component, entity))
                        if len(matches) != 1 or matches[0]['category'] != obj['category']:
                            raise ValueError('高级资源识别结果已变化')
                        obj.update(matches[0])
                    if obj['category'] in ('blueberry', 'raspberry', 'bush', 'tree', 'shrub'):
                        obj.update(map_static=True, map_id=('plant', hierarchy, owner))
                    elif obj['category'] in ('rock', 'ore'):
                        obj.update(map_static=True, map_id=('mineral', hierarchy, owner))
                    objects.append(obj)
                    observations.append((hierarchy, owner, obj))
            except ValueError:
                skipped += 1
                if not discovered:
                    discovery_unknown += 1
        entity_unknown = skipped
        self._phase('barriers')
        try:
            barriers = self.barrier_owners()
            barrier_state = 'ready'
        except ValueError:
            barriers, barrier_state = {}, 'unavailable'
        meta.stage = 'managed_collisions'
        self._phase('managed_collisions')
        colliders, visited, streams, triggers, inactive, unsupported = [], set(), set(), 0, 0, 0
        managed_unknown = 0
        for group in groups:
            self.read_budget.check()
            for collider in meta.dictionary(meta.ref(group, '_colliders')):
                stream = meta.ref(collider, '_stream')
                if stream:
                    streams.add(stream)
        for stream in streams:
            self.read_budget.check()
            for collision in meta.dictionary(meta.ref(stream, '_collisions')):
                for collider in meta.array_refs(meta.ref(collision, 'colliders')):
                    self.read_budget.check()
                    if not collider or collider in visited:
                        continue
                    visited.add(collider)
                    try:
                        info = meta.obj(collider)
                        enabled = meta.field(info['address'], '_enabled')['offset']
                        if meta.data(collider + enabled, 1) != b'\x01':
                            inactive += 1
                            continue
                        if info['name'] not in ('DynamicBoxCollider', 'DynamicSphereCollider'):
                            unsupported += 1
                            continue
                        field = meta.field(info['address'], '_colliderData')
                        data_class = meta.cls(field['type'])
                        start = collider + field['offset'] - 16
                        trigger = meta.field(data_class['address'], 'IsTrigger')['offset']
                        if meta.data(start + trigger, 1) != b'\0':
                            triggers += 1
                            continue
                        if info['name'] != 'DynamicBoxCollider':
                            unsupported += 1
                            continue
                        def vector(name, count):
                            offset = meta.field(data_class['address'], name)['offset']
                            return list(struct.unpack('<' + 'f' * count, meta.data(start + offset, count * 4)))
                        owner = meta.ref(collider, '_group')
                        hierarchy = meta.ref(collider, '_hierarchy')
                        if not hierarchy:
                            hierarchy = self.group_hierarchy(owner, owners)
                        if not hierarchy or hierarchy == local_hierarchy:
                            unsupported += 1
                            continue
                        geometry = box_geometry(vector('Position', 3), vector('Rotation', 4),
                                                vector('Center', 3), vector('Size', 3), world(hierarchy),
                                                allow_planar=True)
                        if geometry.get('surface_kind') in ('zero-x-box-plane', 'zero-y-box-plane', 'zero-z-box-plane'):
                            geometry['source'] = 'verified-managed-planar-box'
                        if meta.data(collider + enabled, 1) != b'\x01' or meta.ref(collider, '_group') != owner:
                            continue
                        category = self.collider_barrier(collider, owner, hierarchy, barriers)
                        if category:
                            geometry['category'] = category
                            geometry['source'] = 'verified-solid-collider'
                        colliders.append(geometry)
                    except ValueError:
                        skipped += 1
                        managed_unknown += 1
        managed_observed_to = time.monotonic()
        self.read_budget.check()
        native_observed_from = time.monotonic()
        self._phase('native_collisions')
        world_stats, world_state = {}, 'unavailable'
        try:
            if self.native is None:
                assembly_base, _, _ = self.memory._module('GameAssembly.dll')
                self.native = NativeWorldCollisions(self.memory._read, assembly_base)
            world_colliders, world_stats = self.native.snapshot()
            colliders.extend(world_colliders)
            world_state = 'ready'
        except ValueError:
            # Streaming replacement invalidates this read; never retain an old
            # world's obstacles. The worker retries with a fresh snapshot.
            self.native = None
        native_observed_to = time.monotonic()
        self._phase('scope_validation')
        self.read_budget.check()
        current = self.memory.snapshot()
        if self.local_hierarchy(current) != local_hierarchy or meta.static_ref(hierarchy_class, 'ActiveHierarchies') != registry:
            raise SceneReadIssue('场景已切换，正在重新读取地图', kind='scope_changed', stage=meta.stage)
        meta.stage = 'optional_definitions'
        self._phase('optional_definitions')
        global_resources, global_state = [], 'unavailable'
        try:
            if getattr(self, 'global_minerals', None) is None:
                self.global_minerals = GlobalMineralReader(meta, self.memory.base, STONE_ITEM_TYPES)
            global_resources = self.global_minerals.snapshot()
            global_state = 'ready'
        except ValueError:
            pass  # Optional display definitions must never discard live navigation geometry.
        house_entrances, house_state, patrol_context = [], 'unavailable', None
        try:
            if getattr(self, 'house_entrances', None) is None:
                self.house_entrances = HouseEntranceReader(meta, self.memory.base, {})
            house_entrances = self.house_entrances.snapshot()
            house_state = 'ready'
            # The main-level identity is stable across streaming collision changes.
            # Keep addresses out of the persisted task context.
            patrol_context = hashlib.sha256(repr((self.memory.pid, meta.domain,
                self.house_entrances.cache['context'])).encode()).hexdigest()
        except (ValueError, OSError):
            pass  # Missing entrances must not discard live objects or collision geometry.
        terrain = ()
        if world_state == 'ready' and global_state == 'ready':
            try:
                terrain = verified_terrain_walls(self.memory.profile.get('game_assembly_sha256'),
                                                 self.native.main_terrain_loaded())
            except ValueError:
                pass  # A changing scene must not receive the previous world's terrain.
        colliders.extend(terrain)
        # Slow geometry work must not publish the object list from its beginning.
        transforms.clear()
        self._phase('final_validation')
        meta.stage = 'registry_end'
        active = self.read_registry(registry, hierarchy_class, local_hierarchy, self.read_budget)
        live_stats = {'unknown': 0}
        objects = self.refresh_live_objects(observations, registry, world, active=active,
                                            stats=live_stats, check=self.read_budget.check)
        current = self.memory.snapshot()
        if self.local_hierarchy(current) != local_hierarchy or meta.static_ref(hierarchy_class, 'ActiveHierarchies') != registry:
            raise SceneReadIssue('场景已切换，正在重新读取地图', kind='scope_changed', stage=meta.stage)
        self.read_budget.check()
        root_verified_at = time.monotonic()
        managed_complete = not (discovery_unknown or managed_unknown)
        quality = {'navigation_eligible': managed_complete and world_state == 'ready',
                   'root_verified_at': root_verified_at,
                   'parts': {
                       'entities': {'state': 'partial' if entity_unknown or live_stats['unknown'] else 'complete',
                                    'observed_from': scan_started, 'observed_to': root_verified_at},
                       'managed_collisions': {'state': 'complete' if managed_complete else 'partial',
                                              'observed_from': scan_started, 'observed_to': managed_observed_to},
                       'native_collisions': {'state': 'complete' if world_state == 'ready' else 'unavailable',
                                             'observed_from': native_observed_from, 'observed_to': native_observed_to}},
                   'unknown_entities': entity_unknown + live_stats['unknown'],
                   'discovery_unknown': discovery_unknown,
                   'managed_unknown': managed_unknown}
        return {'quality': quality, 'navigation_only': getattr(self, 'navigation_only', False),
                'terrain_state': 'ready' if terrain else 'unavailable',
                'navigation_reference': getattr(self, 'navigation_reference', None) if terrain else None,
                'terrain_revision': 'main-terrain-v1' if terrain else None,
                'schema': 'memory-scene-map-v1', 'state': 'ready', 'read_only': True,
                'scope': 'loaded-scene-partial', 'objects': objects, 'colliders': colliders, 'models': models,
                'position': current['position'], 'barrier_state': barrier_state, 'world_state': world_state,
                'global_resources': global_resources, 'global_resource_state': global_state,
                'house_entrances': house_entrances, 'house_entrance_state': house_state,
                'patrol_context': patrol_context,
                'house_exclusions': [], 'house_exclusion_state': 'disabled',
                'global_resource_counts': {category: sum(p['category'] == category for p in global_resources)
                                           for category in ('rock', 'ore')},
                'cache_context': (self.memory.pid, self.native.scene_key) if world_state == 'ready' else None,
                'statistics': {'hierarchies': len(entries), 'terrain_walls': len(terrain),
                    **world_stats,
                    'fences': sum(c.get('category') == 'fence' for c in colliders),
                    'handrails': sum(c.get('category') == 'handrail' for c in colliders),
                    'triggers_filtered': triggers, 'inactive_filtered': inactive,
                    'unsupported_colliders': unsupported, 'skipped': skipped}}

class NavigationSceneMap(MemorySceneMap):
    """Guidance-only reader: same collision/scope checks, no resource markers.

    Each instance owns its metadata and process handle; never shared with the
    full map reader. Optional world/terrain verification remains unchanged.
    """
    navigation_only = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.meta.batch_object_headers = True
