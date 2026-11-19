"""只读读取当前角色已解锁的料理图鉴列表。

CookingSystem._cookingRecipes 是 Dictionary<cookbookId, List<CookingRecipe>>，
只包含角色当前已学会的配方；未解锁的配方不会出现在这里。
"""
import struct

from utils.core import thresholds as policy
from utils.drivers.cooking_recipe import CookingRecipeReader

_COOKING_NS = 'XDTGameSystem.GameplaySystem.Cooking'
_LIST_NS = 'System.Collections.Generic'
_MAX_GROUPS = 256
_MAX_RECIPES = 1024


class CookingUnlockedRecipes:
    def __init__(self, memory):
        self.memory = memory
        self.recipe = CookingRecipeReader(memory)
        self.meta = self.recipe.meta

    @staticmethod
    def _field(meta, info, name, code):
        field = meta.field(info['address'], name)
        if field['code'] != code or field['flags'] & 16 or field['offset'] < 16:
            raise ValueError('已解锁食谱列表字段布局不符')
        return field

    def _group(self, group):
        m = self.meta
        info = m.obj(group)
        if (info['name'], info['namespace']) != ('List`1', _LIST_NS):
            raise ValueError('已解锁食谱分组类型不符')
        size_field = self._field(m, info, '_size', 8)
        version_field = self._field(m, info, '_version', 8)
        size_at, version_at = group + size_field['offset'], group + version_field['offset']
        size, version = m.integer(size_at), m.integer(version_at)
        if not 0 <= size <= policy.COORD_SCENE_MAX_ITEMS:
            raise ValueError('已解锁食谱分组数量无效')
        items = m.ref(group, '_items')
        if not size:
            if m.integer(size_at) != size or m.integer(version_at) != version \
                    or m.ref(group, '_items') != items:
                raise ValueError('已解锁食谱分组在读取时变化')
            return []
        array, capacity = m.array(items, policy.COORD_SCENE_MAX_CAPACITY)
        element = m.cls(array['element'])
        if (element['name'], element['namespace']) != ('CookingRecipe', _COOKING_NS) \
                or size > capacity:
            raise ValueError('已解锁食谱条目类型不符')
        stride = element['size'] - 16
        if stride < 24:
            raise ValueError('已解锁食谱条目布局不符')
        raw = m.data(items + 32, size * stride)
        rows = []
        for i in range(size):
            row = raw[i * stride:(i + 1) * stride]
            recipe_id = struct.unpack_from('<i', row, 0)[0]
            name_at = struct.unpack_from('<Q', row, 8)[0]
            sort_order = struct.unpack_from('<i', row, 16)[0]
            is_new = row[20]
            if recipe_id <= 0 or is_new not in (0, 1):
                raise ValueError('已解锁食谱条目身份无效')
            name = m.managed_string(name_at)
            if not name.strip() or m.managed_string(name_at) != name:
                raise ValueError('已解锁食谱名称正在变化')
            rows.append({'recipe_id': recipe_id, 'name': name,
                         'sort_order': sort_order, 'is_new': bool(is_new)})
        if m.integer(size_at) != size or m.integer(version_at) != version \
                or m.ref(group, '_items') != items or m.data(items + 32, len(raw)) != raw:
            raise ValueError('已解锁食谱分组在读取时变化')
        return rows

    def snapshot(self):
        before = self.memory.locator.snapshot()
        m = self.meta
        context = self.recipe.context()
        system = context[-1]
        dictionary = m.ref(system, '_cookingRecipes')
        if not dictionary:
            raise ValueError('已解锁食谱列表未确认')
        info = m.obj(dictionary)
        if (info['name'], info['namespace']) != ('Dictionary`2', _LIST_NS):
            raise ValueError('已解锁食谱列表类型不符')
        groups, stamp = m.dictionary(dictionary, max_items=_MAX_GROUPS, with_stamp=True)
        recipes = []
        for group in groups:
            recipes.extend(self._group(group))
            if len(recipes) > _MAX_RECIPES:
                raise ValueError('已解锁食谱数量超出读取上限')
        retry = m.dictionary(dictionary, max_items=_MAX_GROUPS, with_stamp=True)
        if retry != (groups, stamp) or self.recipe.context() != context \
                or self.memory.locator.snapshot() != before:
            raise ValueError('已解锁食谱在读取期间变化')
        recipes.sort(key=lambda row: (row['sort_order'], row['recipe_id']))
        return {'count': len(recipes), 'recipes': recipes}
