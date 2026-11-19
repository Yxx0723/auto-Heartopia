"""料理列表只显示游戏内已解锁的料理。"""
from collections import deque
from types import SimpleNamespace

from utils.ui.cooking_catalog_panel import CookingCatalogPanel


class Var:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class List:
    def __init__(self):
        self.items = {}

    def delete(self, *args):
        self.items.clear()

    def insert(self, parent, index, iid, values):
        self.items[iid] = values

    def get_children(self):
        return list(self.items)

    def exists(self, iid):
        return iid in self.items

    def selection_set(self, iid):
        pass

    def selection(self):
        return ()


def make_panel():
    panel = object.__new__(CookingCatalogPanel)
    local = [
        {'recipe_id': 45101, 'name': '田园沙拉', 'category': '日常料理', 'ingredients': [],
         'status': 'confirmed'},
        {'recipe_id': 45105, 'name': '蓝莓果酱', 'category': '日常料理', 'ingredients': [],
         'status': 'confirmed'},
        {'recipe_id': 45561, 'name': '灯笼草莓热可可', 'category': '近幽集',
         'ingredients': [], 'status': 'confirmed'},
    ]
    panel.recipes = local
    panel.by_id = {r['recipe_id']: r for r in local}
    panel.unlocked = [
        {'recipe_id': 45101, 'name': '田园沙拉'},
        {'recipe_id': 45420, 'name': '温泉蛋'},
    ]
    panel.unlocked_ids = {45101, 45420}
    panel.recipe = None
    panel.list = List()
    panel.query = Var()
    panel.category = Var('日常料理')
    panel.show_detail = lambda: None
    panel.owner = SimpleNamespace(view=SimpleNamespace(status={'pid': ''}), cooking=SimpleNamespace())
    return panel


def test_only_unlocked_recipes_are_listed():
    panel = make_panel()
    panel.filter()
    assert panel.list.items == {'45101': ('田园沙拉',)}
    assert {r['recipe_id'] for r in panel.display_recipes} == {45101, 45420}


def test_unlocked_recipe_missing_locally_sits_in_other_category():
    panel = make_panel()
    panel.category.set('其它图鉴')
    panel.filter()
    assert panel.list.items == {'45420': ('温泉蛋',)}
    stub = next(r for r in panel.display_recipes if r['recipe_id'] == 45420)
    assert stub['status'] == 'unconfirmed'


def test_before_unlocked_list_arrives_full_catalog_is_kept():
    panel = make_panel()
    panel.unlocked = None
    panel.filter()
    assert set(panel.list.items) == {'45101', '45105'}
