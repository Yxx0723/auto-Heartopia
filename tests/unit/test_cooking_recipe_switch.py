"""P1 recipe switching: read-only synchronization, no repeated card click."""
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.drivers.cooking_menu_state import CookingMenuDetailMismatch, CookingMenuStateReader
from utils.flows import cooking_selection as selection_flow

RECIPE = {'recipe_id': 45558, 'name': '灯笼橘子热可可', 'status': 'confirmed'}


def _menu_reader(detail):
    r = CookingMenuStateReader.__new__(CookingMenuStateReader)
    r.memory = SimpleNamespace(locator=SimpleNamespace(snapshot=Mock()))
    r.recipe = SimpleNamespace(context=Mock(return_value=(1, 2, 3)),
        meta=SimpleNamespace(domain=9, ref=Mock(return_value=100)))
    refs = {'ui': 30, '_selectedRecipeDetail': detail,
            'background_go': 40, 'list_go': 41, 'btnBar_go': 42}
    fields = {'_viewActiveCache': b'\x01', '_isGotFocus': b'\x01',
        '_cookerStaticId': struct.pack('<i', 370033),
        '_cookerNetId': struct.pack('<I', 7),
        '_cookerObjectId': struct.pack('<Q', 8)}
    r.ui = SimpleNamespace(
        meta=SimpleNamespace(obj=lambda _: {'name': 'CookPanel', 'namespace': 'XDTGame.UI.Panel'},
                             ref=lambda _, key: refs[key]),
        ui_views=lambda: (1, 2, 3, (10,)),
        expected=Mock(),
        payload=lambda _, key, *args: fields[key])
    r.layout = SimpleNamespace(active=lambda _: True)
    return r


@pytest.mark.parametrize('panel_detail', [0, 200])
def test_mismatch_is_specific_value_error_and_preserves_both_references(panel_detail):
    with pytest.raises(CookingMenuDetailMismatch) as ex:
        _menu_reader(panel_detail).snapshot()
    assert isinstance(ex.value, ValueError)
    assert (ex.value.panel_detail, ex.value.system_detail) == (panel_detail, 100)


class Kitchen:
    def __init__(self, mode, mismatch_reads=3):
        self.mode = mode
        self.mismatch_reads = mismatch_reads
        self.clicked = False
        self.reads_after_click = 0
        self.now = 0.
        self.events = []
        self.reports = []
        self.releases = 0
        self.pause_calls = 0
        self.card = {'recipe_id': 45558, 'rect': (10, 20, 30, 40), 'handle': 1}

    def monotonic(self): return self.now
    def sleep(self, dt): self.now += dt
    def size(self): return (100, 100)
    def pointer(self): return (20, 30, True)
    def foreground(self): return True
    def snapshot(self): return (True, False)
    def pause(self): self.pause_calls += 1
    def release_all(self): self.releases += 1
    def execute(self, event):
        self.events.append(event)
        if event['type'] == 'mouse_down':
            self.clicked = True
    def inventory(self): return {'fake': 'inventory'}
    def report(self, category, message, **kwargs):
        self.reports.append((category, kwargs))
    def selection(self, client_size):
        assert client_size == (100, 100)
        if self.mode == 'before_click_mismatch':
            raise CookingMenuDetailMismatch(0, 100)
        if self.clicked:
            self.reads_after_click += 1
            if self.mode == 'persistent_mismatch' or self.reads_after_click <= self.mismatch_reads:
                raise CookingMenuDetailMismatch(0 if self.reads_after_click % 2 else 101, 100)
        data = {'scope': 'S', 'stove_identity': [370033, 7, 8],
                'available_ids': [45557, 45558], 'cards': [self.card],
                'viewport': (1, 1, 99, 99)}
        selected_id = 45557 if not self.clicked or self.mode == 'wrong_selected' else 45558
        data['selected'] = {'recipe_id': selected_id,
                            'name': '灯笼苹果热可可' if selected_id == 45557 else '灯笼橘子热可可'}
        data['signature'] = (selected_id, 'stable')
        if self.clicked and self.mode == 'wrong_scene':
            data['scope'] = 'OTHER'
        return data


def run(k, monkeypatch, timeout=3.):
    monkeypatch.setattr(selection_flow, 'material_status',
                        lambda recipe, inventory: {'sufficient': True})
    return selection_flow.select_recipe(
        recipe=RECIPE, reader=k, window=k, controls=k,
        device=k, inventory=k.inventory, clock=k, report=k.report,
        timeout=timeout)


def left_clicks(k):
    return sum(e['type'] == 'mouse_down' for e in k.events)


def test_switch_recovers_and_confirms_selected_id_after_transient_mismatch(monkeypatch):
    k = Kitchen('recover', mismatch_reads=3)
    result = run(k, monkeypatch)
    assert result['recipe_id'] == 45558 and result['started_pots'] == 0
    assert left_clicks(k) == 1
    assert any(category == 'recipe_detail_sync_pending' for category, _ in k.reports)
    assert any(category == 'recipe_detail_sync_recovered' for category, _ in k.reports)
    assert any(category == 'RECIPE_SELECTED' for category, _ in k.reports)
    assert k.now < 3.0


def test_persistent_mismatch_stops_at_original_deadline_without_reclick(monkeypatch):
    k = Kitchen('persistent_mismatch')
    with pytest.raises(ValueError, match='详情未同步'):
        run(k, monkeypatch, timeout=1.)
    assert left_clicks(k) == 1
    assert k.now <= 1.01
    assert not any(category == 'RECIPE_SELECTED' for category, _ in k.reports)


def test_mismatch_before_user_selection_cannot_be_ignored(monkeypatch):
    k = Kitchen('before_click_mismatch')
    with pytest.raises(CookingMenuDetailMismatch):
        run(k, monkeypatch)
    assert left_clicks(k) == 0


def test_mismatched_selected_recipe_id_never_enters_p2_or_reclicks(monkeypatch):
    k = Kitchen('wrong_selected', mismatch_reads=2)
    with pytest.raises(ValueError, match='选中确认超时'):
        run(k, monkeypatch, timeout=1.)
    assert left_clicks(k) == 1
    assert not any(category == 'RECIPE_SELECTED' for category, _ in k.reports)


def test_stove_or_scene_change_is_not_treated_as_recipe_sync(monkeypatch):
    k = Kitchen('wrong_scene', mismatch_reads=0)
    with pytest.raises(ValueError, match='灶台或场景'):
        run(k, monkeypatch, timeout=2.)
    assert left_clicks(k) == 1
    assert not any(category == 'RECIPE_SELECTED' for category, _ in k.reports)


def test_no_mismatch_regular_switch_still_works(monkeypatch):
    k = Kitchen('recover', mismatch_reads=0)
    assert run(k, monkeypatch)['recipe_id'] == 45558
    assert left_clicks(k) == 1
    assert not any(category == 'recipe_detail_sync_pending' for category, _ in k.reports)
