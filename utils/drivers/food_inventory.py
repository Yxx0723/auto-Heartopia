"""Read the owned backpack and its Eat actions, never warehouse/recipe hints."""
import ctypes
from ctypes import wintypes
import struct
import time

from utils.drivers.mono_scene import SceneMetadata
from utils.drivers.tool_durability import ToolDurabilityReader
from utils.drivers.global_minerals import GlobalMineralReader

NS = 'XDTGameSystem.GameplaySystem.BackPack'
OWNER = '<XDTGame.Core.IModule.Owner>k__BackingField'
# Observed TableBagitem names on the fingerprint-pinned build, 2026-10-22.
# This is a name cache, not an assertion that an item is owned or edible.
KNOWN_NAMES = {40101: '苹果', 40201: '橘子', 40501: '蓝莓', 40502: '树莓',
               40601: '海芦笋', 40602: '海葡萄', 40603: '裙带菜',
               45420: '温泉蛋', 45999: '大厨特制沙拉', 48001: '平菇',
               48002: '香菇', 48003: '口蘑', 48004: '牛肝菌',
               48005: '黑松露', 48007: '蜜环菌'}
# The pinned client's BackpackItemFunction.Eat, independently observed on
# apples, berries, mushrooms and cooked foods. Learn/Use/Place are excluded.
EAT_ACTION = 112
# BackpackItem.entityType on this pinned client: cooked dishes (including
# spring eggs and chef salad) are 45; fruit is 25, raw mushrooms are 97.
PREPARED_FOOD_TYPE = 45


class MemoryInfo(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('allocation', ctypes.c_void_p),
                ('allocation_protect', wintypes.DWORD), ('partition', wintypes.WORD),
                ('size', ctypes.c_size_t), ('state', wintypes.DWORD),
                ('protect', wintypes.DWORD), ('kind', wintypes.DWORD)]


def private_blocks(memory, check):
    """Bounded readable private regions; reuse the existing VM_READ handle."""
    query = memory.kernel.VirtualQueryEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MemoryInfo), ctypes.c_size_t]
    query.restype = ctypes.c_size_t
    address = 0
    while address < 0x7fffffffffff:
        check()
        info = MemoryInfo()
        if not query(memory.handle, address, ctypes.byref(info), ctypes.sizeof(info)):
            break
        end = (info.base or 0)+info.size
        if end <= address:
            break
        if info.state == 0x1000 and info.kind == 0x20000 and not info.protect & 0x101 \
                and info.protect & 0xff in (4, 8, 0x40, 0x80):
            for start in range(info.base, end, 2*1024**2):
                yield start, min(2*1024**2, end-start)
        address = end


class FoodInventoryReader:
    def __init__(self, memory):
        self.memory = memory
        self.meta = SceneMetadata(memory._read, memory.locator.domain)
        self.world_class = ToolDurabilityReader(memory._read, memory.base, memory.locator.domain).world_class
        self.names, self.name_blocks = dict(KNOWN_NAMES), None

    def expected(self, obj, name, namespace):
        ci = self.meta.obj(obj)
        if (ci['name'], ci['namespace']) != (name, namespace):
            raise ValueError('背包对象类型不符')
        return ci

    def offset(self, ci, name, code, width):
        f = self.meta.field(ci['address'], name)
        if f['flags'] & 16 or f['code'] != code or not 16 <= f['offset'] <= ci['size']-width:
            raise ValueError('背包物品字段布局不符')
        return f['offset']

    def dictionary_rows(self, obj):
        m = self.meta
        ci = self.expected(obj, 'Dictionary`2', 'System.Collections.Generic')
        offsets = [self.offset(ci, n, 8, 4) for n in ('_version', '_count', '_freeCount')]
        def stamp():
            return (*[m.integer(obj+o) for o in offsets], m.ref(obj, '_entries'))
        before = stamp()
        _, count, free, array = before
        if not 0 <= free <= count <= 4096:
            raise ValueError('背包物品数量无效')
        if count == free:
            return [], lambda: stamp() == before
        ai, capacity = m.array(array, 8192)
        entry = m.cls(ai['element'])
        stride = entry['size']-16
        vf = m.field(entry['address'], 'value')
        if count > capacity or vf['code'] != 17 or not 16 <= stride <= 256:
            raise ValueError('背包列表不是物品记录')
        item = m.cls(vf['type'])
        if (item['name'], item['namespace']) != ('BackpackItem', 'XDTGameSystem.UISystem.BackPack'):
            raise ValueError('背包列表物品归属不符')
        nxt = self.offset(entry, 'next', 8, 4)-16
        value = self.offset(entry, 'value', 17, item['size']-16)-16
        raw = m.data(array+32, count*stride)
        rows = [raw[i*stride+value:i*stride+value+item['size']-16] for i in range(count)
                if struct.unpack_from('<i', raw, i*stride+nxt)[0] >= -1]
        if len(rows) != count-free or stamp() != before:
            raise ValueError('背包列表正在更新')
        return [(item, row) for row in rows], lambda: stamp() == before and m.data(array+32, len(raw)) == raw

    def actions(self, obj):
        m = self.meta
        ci = self.expected(obj, 'List`1', 'System.Collections.Generic')
        size_at = obj+self.offset(ci, '_size', 8, 4)
        version_at = obj+self.offset(ci, '_version', 8, 4)
        size, version, array = m.integer(size_at), m.integer(version_at), m.ref(obj, '_items')
        ai, capacity = m.array(array, 128)
        element = m.cls(ai['element'])
        if (element['name'], element['namespace'], element['size']) != ('BackpackItemFunction', NS, 20) \
                or 'Eat' not in element['fields'] or not 0 <= size <= capacity <= 128:
            raise ValueError('背包食用动作类型不符')
        raw = m.data(array+32, size*4) if size else b''
        result = struct.unpack('<'+'i'*size, raw) if size else ()
        if (m.integer(size_at), m.integer(version_at), m.ref(obj, '_items')) != (size, version, array):
            raise ValueError('背包食用动作正在更新')
        return result

    def discover_names(self, wanted, check):
        """Unknown foods get a cancellable, incremental name-only lookup.

        No address found here authorizes consumption: owned stock + Eat action
        and the actual in-game 食用 button are independently required.
        """
        missing = wanted-self.names.keys()
        if not missing:
            return
        m = self.meta
        finder = GlobalMineralReader(m, self.memory.base, {})
        ci = m.cls(finder.find_class('EcsClient', 'TableBagitem', ''))
        vtable = m.pointer(ci['address']+0xd8)
        if not vtable or m.pointer(vtable) != ci['address'] or m.pointer(vtable+16) != m.domain:
            raise ValueError('食物名称表尚未就绪')
        identity, name_at = self.offset(ci, 'id', 8, 4), self.offset(ci, 'name', 14, 8)
        if self.name_blocks is None:
            self.name_blocks = iter(private_blocks(self.memory, check))
        started, scanned = time.monotonic(), 0
        while missing and time.monotonic()-started < 1. and scanned < 128*1024**2:
            check()
            block = next(self.name_blocks, None)
            if block is None:
                self.name_blocks = None
                break
            base, size = block
            scanned += size
            raw = self.memory._read(base, size)
            if raw is None:
                continue
            offset = raw.find(vtable.to_bytes(8, 'little'))
            while offset >= 0:
                check()
                obj = base+offset
                if obj % 8 == 0:
                    try:
                        item_id = m.integer(obj+identity)
                        if item_id in missing and m.obj(obj)['address'] == ci['address']:
                            pointer = m.pointer(obj+name_at)
                            name = m.managed_string(pointer)
                            if 1 <= len(name) <= 32 and not any(ord(c) < 32 for c in name) \
                                    and m.integer(obj+identity) == item_id and m.pointer(obj+name_at) == pointer:
                                self.names[item_id] = name
                                missing.remove(item_id)
                    except (ValueError, UnicodeError):
                        pass
                offset = raw.find(vtable.to_bytes(8, 'little'), offset+8)
        if missing:
            raise ValueError('正在核对新食物名称，稍后自动重试')

    def owned_rows(self, check=lambda: None):
        m = self.meta
        check()
        self.memory.locator.snapshot()  # Require a valid current local role.
        node = m.static_ref(self.world_class, '_currentLevel')
        owner, modules = m.ref(node, '_instance'), m.ref(node, '_viewModules')
        if m.ref(modules, 'gameLevel') != node:
            raise ValueError('背包场景归属不符')
        group = m.ref(modules, 'group')
        array = m.ref(group, '_modules')
        members = m.array_refs(array)
        matches = [p for p in members if p and (m.obj(p)['name'], m.obj(p)['namespace']) ==
                   ('BagModule', 'XDTLevelAndEntity.Game.Module.Bag')]
        if len(matches) != 1:
            raise ValueError('本地背包模块不存在或存在歧义')
        module = matches[0]
        system = m.ref(module, 'BackPackSystem')
        self.expected(system, 'BackPackSystem', NS)
        if m.ref(module, OWNER) != owner or m.ref(system, OWNER) != owner:
            raise ValueError('背包不属于当前场景')
        stores = m.ref(system, '_storages')
        stores_list = m.dictionary(stores, max_items=32)
        bags = [p for p in stores_list if (m.obj(p)['name'], m.obj(p)['namespace']) == ('StorageBackpack', NS)]
        if len(bags) != 1:
            raise ValueError('随身背包尚未就绪')
        bag = bags[0]
        items = m.ref(bag, 'items')
        rows, stable = self.dictionary_rows(items)
        def unchanged():
            if (not stable() or m.ref(bag, 'items') != items or m.ref(system, '_storages') != stores
                    or m.dictionary(stores, max_items=32) != stores_list or m.ref(module, 'BackPackSystem') != system
                    or m.ref(module, OWNER) != owner or m.ref(system, OWNER) != owner
                    or m.ref(node, '_instance') != owner or m.ref(node, '_viewModules') != modules
                    or m.ref(modules, 'group') != group or m.ref(group, '_modules') != array
                    or m.array_refs(array) != members or m.static_ref(self.world_class, '_currentLevel') != node
                    or m.pointer(self.memory.base+0x4d3878) != m.domain):
                raise ValueError('背包在读取期间发生变化，稍后重试')
            return True
        return rows, unchanged, (node, owner, module, system, bag)

    def snapshot(self, check=lambda: None):
        rows, stable, context = self.owned_rows(check)
        foods, locked_variants, locked_ids, uncertain_locked = [], set(), set(), set()
        for ci, raw in rows:
            check()
            def value(name, code=8, fmt='i', width=4):
                return struct.unpack_from('<'+fmt, raw, self.offset(ci, name, code, width)-16)[0]
            count = value('count')
            if not 0 <= count <= 100000000:
                raise ValueError('背包食物数量无效')
            locked, disabled = value('isLock', 2, 'B', 1), value('IsDisable', 2, 'B', 1)
            if count and (locked not in (0,1) or disabled not in (0,1)):
                raise ValueError('背包锁定或禁用状态未确认')
            if count and (locked or disabled):
                identity, star = value('staticId'), value('_starRate')
                locked_ids.add(identity)
                locked_variants.add((identity, star))
                if not 0 <= star <= 5:
                    uncertain_locked.add(identity)
                continue
            if not count:
                continue
            functions = value('functions', 21, 'Q', 8)
            if functions and EAT_ACTION in self.actions(functions):
                foods.append({'item_id': value('staticId'), 'net_id': value('netId', 9, 'I'),
                              'count': count, 'star': value('_starRate'), 'edible': True,
                              'entity_type': value('entityType', 17),
                              'prepared': value('entityType', 17) == PREPARED_FOOD_TYPE})
        for food in foods:
            food['ambiguous_locked_variant'] = ((food['item_id'],food['star']) in locked_variants
                                                 or food['item_id'] in uncertain_locked)
            food['verify_star'] = food['item_id'] in locked_ids
        self.discover_names({f['item_id'] for f in foods}, check)
        stable()
        self.context = context[:4]
        return [dict(f, name=self.names[f['item_id']]) for f in foods]

    def counts_snapshot(self, check=lambda: None):
        """Owned backpack totals, including locked items; no names scan or warehouse."""
        import hashlib
        rows, stable, context = self.owned_rows(check)
        counts = {}
        for ci, raw in rows:
            check()
            identity = struct.unpack_from('<i', raw, self.offset(ci, 'staticId', 8, 4)-16)[0]
            count = struct.unpack_from('<i', raw, self.offset(ci, 'count', 8, 4)-16)[0]
            if identity <= 0 or not 0 <= count <= 100000000:
                raise ValueError('背包物品编号或数量无效')
            counts[identity] = counts.get(identity, 0) + count
        stable()
        # Opaque per-process scope: neither dictionary resize nor stack ordering changes it.
        scope = hashlib.sha256(repr((self.meta.domain, context)).encode()).hexdigest()
        return {'scope': scope, 'counts': counts}

    def cooking_snapshot(self, max_star=1, with_names=False, check=lambda: None):
        """Stock plus conservative selectable ingredients, never warehouse stock.

        If a protected variant shares an id, reject that entire id: the cooking
        UI's automatic stack choice has not been verified to respect variants.
        """
        if type(max_star) is not int or not 0 <= max_star <= 5:
            raise ValueError('可用原料最高星级无效')
        rows, stable, context = self.owned_rows(check)
        counts, usable, protected, items = {}, {}, set(), []
        for ci, raw in rows:
            check()
            def value(name, code=8, fmt='i', width=4):
                return struct.unpack_from('<'+fmt, raw, self.offset(ci, name, code, width)-16)[0]
            identity, count, star = value('staticId'), value('count'), value('_starRate')
            locked, disabled = value('isLock', 2, 'B', 1), value('IsDisable', 2, 'B', 1)
            if identity <= 0 or not 0 <= count <= 100000000 or locked not in (0, 1) or disabled not in (0, 1):
                raise ValueError('背包原料数量或保护状态未确认')
            if not count:
                continue
            counts[identity] = counts.get(identity, 0)+count
            if locked or disabled or not 0 <= star <= max_star:
                protected.add(identity)
            else:
                usable[identity] = usable.get(identity, 0)+count
            items.append({'item_id': identity, 'count': count, 'star': star,
                          'locked': bool(locked), 'disabled': bool(disabled),
                          'prepared': value('entityType', 17) == PREPARED_FOOD_TYPE})
        if with_names:
            self.discover_names(set(counts), check)
            items = [dict(item, name=self.names[item['item_id']]) for item in items]
        stable()
        import hashlib
        scope = hashlib.sha256(repr((self.meta.domain, context)).encode()).hexdigest()
        return {'scope': scope, 'counts': counts, 'items': items,
                'usable_counts': {i: n for i, n in usable.items() if i not in protected},
                'protected_ids': sorted(protected)}

    def search_query(self):
        if not getattr(self, 'context', None):
            raise ValueError('尚未读取当前背包')
        m = self.meta
        node, owner, module, system = self.context
        if (m.static_ref(self.world_class, '_currentLevel') != node or m.ref(node, '_instance') != owner
                or m.ref(module, 'BackPackSystem') != system or m.ref(system, OWNER) != owner):
            raise ValueError('搜索期间背包已切换')
        ci = self.expected(system, 'BackPackSystem', NS)
        at = system+self.offset(ci, 'searchingText', 14, 8)
        pointer = m.pointer(at)
        name = m.managed_string(pointer).strip()
        if m.pointer(at) != pointer:
            raise ValueError('背包搜索文字正在更新')
        return name
