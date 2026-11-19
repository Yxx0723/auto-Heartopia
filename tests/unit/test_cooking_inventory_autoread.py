"""选料理后自动读一次背包；有数据或在任务中不重复读。"""
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

    def read_inventory(self, pid):
        self.calls.append(pid)
        return True


def test_selection_reads_inventory_with_string_pid():
    panel = make_panel()
    panel.owner.cooking = Cooking()
    panel.list.selected = ('45101',)
    panel.show_detail = lambda: None

    panel.changed()

    assert panel.owner.cooking.calls == [12345]
    assert panel.stock_button.text == '正在读取背包材料…'


def test_existing_inventory_and_active_task_do_not_reread():
    panel = make_panel()
    panel.owner.cooking = Cooking()
    panel.inventory = {'counts': {}, 'selectable_counts': {}}
    panel.show_detail = lambda: None
    panel.auto_read_inventory()
    assert panel.owner.cooking.calls == []

    panel.inventory = None
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
