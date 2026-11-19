"""背包静默同步：定时刷新只更新食材详情，不刷运行日志。"""
from collections import deque
from types import SimpleNamespace

from utils.ui.cooking_catalog_panel import CookingCatalogPanel


class Button:
    def __init__(self):
        self.text = None

    def configure(self, **kwargs):
        if 'text' in kwargs:
            self.text = kwargs['text']


class Detail:
    def __init__(self, mapped=True):
        self.mapped = mapped
        self.scheduled = []

    def winfo_ismapped(self):
        return self.mapped

    def after(self, ms, fn):
        self.scheduled.append((ms, fn))


class Var:
    def __init__(self):
        self.value = ''

    def set(self, value):
        self.value = value


class Cooking:
    def __init__(self):
        self.calls = []

    def read_inventory(self, pid, *, silent=False):
        self.calls.append((pid, silent))
        return True


def make_panel(mapped=True):
    panel = object.__new__(CookingCatalogPanel)
    panel.inventory = None
    panel.recipe = None
    panel.active = False
    panel.detail = Detail(mapped)
    panel.stock_button = Button()
    panel.runtime_status = Var()
    panel.batch_progress = Var()
    panel.last_log_line = ''
    panel.last_error_note = ''
    panel.last_success = ''
    panel.last_batch = {}
    panel.log_buffer = deque()
    panel.log_views = []
    panel.inventory_auto_ms = 5000
    panel.owner = SimpleNamespace(
        view=SimpleNamespace(status={'pid': '12345'}),
        cooking=Cooking(),
    )
    return panel


def test_silent_read_uses_keyword_and_keeps_button_text():
    panel = make_panel()
    panel.stock_button.text = '刷新背包材料'
    panel.auto_read_inventory(silent=True)
    assert panel.owner.cooking.calls == [(12345, True)]
    assert panel.stock_button.text == '刷新背包材料'


def test_visible_idle_page_tick_reads_and_reschedules():
    panel = make_panel()
    panel._auto_inventory_tick()
    assert panel.owner.cooking.calls == [(12345, True)]
    assert panel.detail.scheduled == [(5000, panel._auto_inventory_tick)]


def test_hidden_or_busy_page_tick_does_not_read():
    hidden = make_panel(mapped=False)
    hidden._auto_inventory_tick()
    assert hidden.owner.cooking.calls == []

    busy = make_panel()
    busy.active = True
    busy._auto_inventory_tick()
    assert busy.owner.cooking.calls == []


def test_silent_events_do_not_reach_runtime_log():
    panel = make_panel()
    panel.apply_log_events([
        {'state': 'inventory_ready', 'silent': True, 'message': '已读取当前随身背包材料'},
        {'state': 'inventory_error', 'silent': True, 'message': '背包材料未确认'},
    ])
    assert list(panel.log_buffer) == []


def test_silent_error_does_not_break_refresh_button():
    panel = make_panel()
    panel.stock_button.text = '刷新背包材料'
    panel.apply_result({'state': 'inventory_error', 'silent': True})
    assert panel.stock_button.text == '刷新背包材料'


def test_batch_end_reads_back_inventory_after_tick():
    panel = make_panel()
    batch = {'target_pots': 2, 'current_pot': 2, 'completed_pots': 2,
             'total_products': 2}
    panel.apply_result({'state': 'batch_finished', 'batch': batch})
    assert len(panel.detail.scheduled) == 1
    ms, callback = panel.detail.scheduled[0]
    assert ms == 400
    callback()
    assert panel.owner.cooking.calls == [(12345, True)]
