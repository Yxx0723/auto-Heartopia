"""Read the current owned stove detail; never select recipes or invoke game methods."""
import hashlib
import struct

from utils.core import thresholds as policy
from utils.drivers.food_inventory import FoodInventoryReader, OWNER
from utils.drivers.global_minerals import GlobalMineralReader


NS = 'XDTGameSystem.GameplaySystem.Cooking'


class CookingRecipeReader:
    def __init__(self, memory):
        self.memory = memory
        self.food = FoodInventoryReader(memory)
        self.meta = self.food.meta
        finder = GlobalMineralReader(self.meta, memory.base, {})
        self.klass = self.meta.cls(finder.find_class('XDTGameSystem', 'CookingSystem', NS))

    def expected(self, obj, name):
        ci = self.meta.obj(obj)
        if (ci['name'], ci['namespace']) != (name, NS):
            raise ValueError('默认食谱对象类型不符')
        return ci

    def context(self):
        m = self.meta
        if m.pointer(self.memory.base+0x4d3878) != m.domain:
            raise ValueError('烹饪运行域已变化')
        node = m.static_ref(self.food.world_class, '_currentLevel')
        level = m.ref(node, '_instance')
        system = m.static_ref(self.klass['parent'], '_instance')
        self.expected(system, 'CookingSystem')
        if not level or m.ref(system, OWNER) != level:
            raise ValueError('灶台不属于当前场景')
        return node, level, system

    def snapshot(self):
        self.memory.locator.snapshot()
        m = self.meta
        context = self.context()
        system = context[-1]
        detail = m.ref(system, '_recipeDetail')
        scope = hashlib.sha256(repr((m.domain, context)).encode()).hexdigest()
        result = {'scope': scope, 'selected': None, 'signature': None}
        checks = []
        if detail:
            ci = self.expected(detail, 'CookingRecipeDetail')
            raw = m.data(detail+16, ci['size']-16)
            checks.append((detail, ci, raw))
            def value(info, data, name, code, fmt, width):
                at = self.food.offset(info, name, code, width)-16
                return struct.unpack_from('<'+fmt, data, at)[0]
            identity = value(ci, raw, 'staticId', 8, 'i', 4)
            name_at = value(ci, raw, 'name', 14, 'Q', 8)
            name = m.managed_string(name_at)
            array = value(ci, raw, 'materialSlots', 29, 'Q', 8)
            _, length = m.array(array, policy.COOKING_MAX_MATERIAL_SLOTS)
            refs = m.array_refs(array)
            if not 1 <= length <= policy.COOKING_MAX_MATERIAL_SLOTS or len(refs) != length:
                raise ValueError('默认材料槽数量无效')
            slots = []
            for obj in refs:
                info = self.expected(obj, 'MaterialSlot')
                data = m.data(obj+16, info['size']-16)
                checks.append((obj, info, data))
                filled = value(info, data, 'filled', 2, 'B', 1)
                net = value(info, data, 'filledMaterialNetId', 9, 'I', 4)
                item = value(info, data, 'filledMaterialStaticId', 8, 'i', 4)
                if filled not in (0, 1) or filled and (net <= 0 or item <= 0):
                    raise ValueError('默认材料身份无效')
                slots.append({'filled': bool(filled), 'net_id': net, 'item_id': item})
            if identity <= 0 or not name.strip():
                raise ValueError('默认食谱尚未就绪')
            result.update(selected={'recipe_id': identity, 'name': name, 'slots': slots},
                          signature=(identity, name, tuple((s['filled'], s['net_id'], s['item_id']) for s in slots)))
            if m.array_refs(array) != refs or m.managed_string(name_at) != name:
                raise ValueError('默认食谱正在更新')
        for obj, ci, raw in checks:
            if m.obj(obj)['address'] != ci['address'] or m.data(obj+16, len(raw)) != raw:
                raise ValueError('默认食谱正在更新')
        if self.context() != context or m.ref(system, '_recipeDetail') != detail:
            raise ValueError('灶台或场景正在切换')
        self.memory.locator.snapshot()
        return result
