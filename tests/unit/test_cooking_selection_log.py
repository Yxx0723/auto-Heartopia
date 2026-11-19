"""切料理时日志只写选了哪个料理，不再写「背包已读取」。"""
from collections import deque
from types import SimpleNamespace

from utils.ui.cooking_catalog_panel import CookingCatalogPanel


class FakeLog:
    def __init__(self):
        self.chunks = []

    def yview(self):
        return (0.0, 1.0)

    def configure(self, **kwargs):
        pass

    def insert(self, index, *chunks):
        self.chunks.extend(chunks)

    def index(self, index):
        return '1.0'

    def delete(self, *args):
        pass

    def see(self, *args):
        pass

    def text(self):
        return ''.join(part for part in self.chunks if isinstance(part, str))


class Cooking:
    def __init__(self):
        self.calls = []

    def read_inventory(self, pid, *, silent=False):
        self.calls.append((pid, silent))
        return True


def make_panel(selected):
    panel = object.__new__(CookingCatalogPanel)
    panel.inventory = None
    panel.active = False
    panel.recipe = None
    panel.recipes = [
        {'recipe_id': 45105, 'name': '蓝莓果酱', 'ingredients': []},
        {'recipe_id': 45101, 'name': '田园沙拉', 'ingredients': []},
    ]
    panel.list = SimpleNamespace(selection=lambda: (selected,))
    panel.runtime_log = FakeLog()
    panel.log_buffer = deque()
    panel.log_views = []
    panel.last_log_line = ''
    panel.show_detail = lambda: None
    panel.owner = SimpleNamespace(
        view=SimpleNamespace(status={'pid': '12345'}),
        cooking=Cooking(),
    )
    return panel


def test_selection_logs_chosen_recipe_only():
    panel = make_panel('45105')
    panel.changed()
    written = panel.runtime_log.text()
    assert '[已选料理]' in written
    assert '蓝莓果酱' in written
    assert '背包已读取' not in written
    assert panel.owner.cooking.calls == [(12345, True)]
    assert list(panel.log_buffer)[0][0] in written


def test_no_selection_writes_no_log():
    panel = make_panel('99999')
    panel.changed()
    assert panel.runtime_log.text() == ''
