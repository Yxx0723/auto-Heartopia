"""Offline recipe catalogue and allocation against the actual carried stock."""
import json
import sys
from pathlib import Path


def catalog_path():
    root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
    return root/'data/cooking/cooking_catalog.json'


def load_catalog(path=None):
    data = json.loads(Path(path or catalog_path()).read_text(encoding='utf-8-sig'))
    if data.get('schema_version') != 1:
        raise ValueError('料理库版本不支持')
    seen = set()
    for recipe in data['recipes']:
        identity = recipe['recipe_id']
        if type(identity) is not int or identity <= 0 or identity in seen:
            raise ValueError('料理库编号无效或重复')
        seen.add(identity)
        if recipe['category'] not in ('日常料理', '近幽集') or not recipe['name']:
            raise ValueError('料理库名称或分类无效')
        for material in recipe['ingredients']:
            if type(material['quantity']) is not int or not 1 <= material['quantity'] <= 4:
                raise ValueError('料理材料数量无效')
            if any(type(i) is not int or i <= 0 for i in material['allowed_item_ids']):
                raise ValueError('料理材料物品编号无效')
    return data


def base_recipe(recipe):
    """Resolve the recipe card separately from the requested product."""
    if 'base_recipe_id' not in recipe:
        return recipe
    candidates = [entry for entry in load_catalog()['recipes']
                  if entry['recipe_id'] == recipe['base_recipe_id']]
    if (len(candidates) != 1 or candidates[0]['status'] != 'confirmed'
            or 'base_recipe_id' in candidates[0]
            or candidates[0]['cooker_type'] != recipe['cooker_type']):
        raise ValueError('变种料理的基础食谱未确认')
    return candidates[0]


def material_status(recipe, inventory):
    """Match every material slot once; overlapping groups cannot share a unit."""
    if not isinstance(inventory, dict) or not inventory.get('scope'):
        return {'sufficient': False, 'known': False, 'materials': []}
    counts = inventory['counts']; available = inventory['selectable_counts']
    materials = []
    slots = []
    for material in recipe['ingredients']:
        candidates = material['allowed_item_ids']
        materials.append({**material, 'owned': sum(counts.get(i, 0) for i in candidates),
                          'available': sum(available.get(i, 0) for i in candidates)})
        slots.extend([tuple(i for i in candidates if available.get(i, 0) > 0)]*material['quantity'])
    slots.sort(key=len)
    remaining = dict(available)
    def assign(index):
        if index == len(slots):
            return True
        for item in slots[index]:
            if remaining.get(item, 0) <= 0:
                continue
            remaining[item] -= 1
            if assign(index+1):
                return True
            remaining[item] += 1
        return False
    confirmed = recipe.get('status') == 'confirmed' and recipe.get('product_id') is not None
    return {'known': True, 'sufficient': bool(confirmed and assign(0)), 'materials': materials}
