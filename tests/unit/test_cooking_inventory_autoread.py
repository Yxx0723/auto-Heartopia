"""每次选料理都重读背包；任务进行中或未连接时不读。"""
from types import SimpleNamespace

from utils.ui.cooking_catalog_panel import CookingCatalogPanel


class Button:
    def configure(self, **kwargs):
        self.text = kwargs.get('text', getattr(self, 'text', ''))


class Var:
    def __init__(self, value=''):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class Listbox:
    def __init__(self, ids):
        self.ids = ids
        self.selected = ()

    def selection(self):
        return self.selected

    def bind(self, *_):
        pass


def make_panel(recipe_id='45101', pid='12345'):
    panel = object.__new__(CookingCatalogPanel)
    panel.inventory = None
    panel.active = False
    panel.recipe = None
    panel.recipes = [{'recipe_id': 45101, 'name': '田园沙拉', 'ingredients': []}]
    panel.list = Listbox(('45101',))
    panel.stock_button = Button()
    panel.owner = SimpleNamespace(
        view=SimpleNamespace(status={'pid': pid}),
        cooking=SimpleNamespace(),
    )
    return panel


class Cooking:
    def __init__(self):
        self.calls = []

    def read_inventory(self, pid, *, silent=False):
        self.calls.append((pid, silent))
        return True


def test_selection_reads_inventory_with_string_pid():
    panel = make_panel()
    panel.owner.cooking = Cooking()
    panel.list.selected = ('45101',)
    panel.show_detail = lambda: None

    panel.changed()

    assert panel.owner.cooking.calls == [(12345, True)]
    assert getattr(panel.stock_button, 'text', None) is None  # 静默刷新不改按钮文案


def test_every_selection_rereads_even_with_inventory_present():
    panel = make_panel()
    panel.owner.cooking = Cooking()
    panel.inventory = {'counts': {}, 'selectable_counts': {}}
    panel.show_detail = lambda: None

    panel.auto_read_inventory()
    panel.auto_read_inventory()

    assert panel.owner.cooking.calls == [(12345, False), (12345, False)]


def test_active_task_does_not_read():
    panel = make_panel()
    panel.owner.cooking = Cooking()
    panel.active = True

    panel.auto_read_inventory()

    assert panel.owner.cooking.calls == []


def test_no_pid_does_not_read():
    panel = make_panel(pid='')
    panel.owner.cooking = Cooking()
    panel.auto_read_inventory()
    assert panel.owner.cooking.calls == []

    panel.owner.view.status['pid'] = 'not-a-pid'
    panel.auto_read_inventory()
    assert panel.owner.cooking.calls == []
