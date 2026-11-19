"""卡片回收时整份读取作废并可只读重扫，不再直接停批。"""
from types import SimpleNamespace

import pytest

from utils.core.scene_read import SceneReadIssue
from utils.drivers import cooking_selection as driver
from utils.drivers.cooking_selection import CookingSelectionReader
from utils.flows.cooking_selection import select_recipe


class FakeMeta:
    """只实现 selection() 走到卡片扫描所需的读取。"""

    def __init__(self, card, scroll, panel, nodes):
        self.card, self.scroll, self.panel, self.nodes = card, scroll, panel, nodes

    def obj(self, address):
        if address == self.card:
            return {'name': 'NewCardWidget', 'namespace': 'XDTGame.UI.Widget'}
        if address == self.panel:
            return {'name': 'CookPanel'}
        raise AssertionError('unexpected obj %r' % (address,))

    def ref(self, obj, name):
        if obj == self.panel and name == 'ui':
            return self.nodes
        if obj == self.nodes and name == 'recipes_scroll':
            return self.scroll
        if obj == self.scroll and name == '_ownerUIView':
            return self.panel
        if obj == 'ui_manager' and name == '<CanvasRect>k__BackingField':
            return 'canvas'
        if obj == self.scroll and name == '_scrollSystem':
            return 'scrollSystem'
        if obj == self.scroll and name == '_handleToData':
            return 'handleToData'
        if obj == self.scroll and name == '_rootToComponent':
            return 'rootToComponent'
        if obj == self.card and name == '_scrollSystem':
            # 卡片已被回收，对象头读到的域不再是场景域。
            raise ValueError('场景对象归属已变化')
        raise AssertionError('unexpected ref %r' % ((obj, name),))

    def dictionary(self, address, max_items=256):
        return [self.card]


def make_reader(monkeypatch):
    card, scroll, panel, nodes = 0x1000, 0x2000, 0x3000, 0x4000
    reader = object.__new__(CookingSelectionReader)
    reader.ui = SimpleNamespace(meta=FakeMeta(card, scroll, panel, nodes),
                                ui_views=lambda: [None, 'ui_manager', None, [panel]])
    reader.layout = SimpleNamespace(
        native=SimpleNamespace(ref=lambda obj, name: 'native:'+name),
        resolve=lambda target, *_: 'scroll_target',
        rect=lambda obj: {'rect': (0, 0, 100, 100)},
        transforms=lambda obj: [],
        native_rect=lambda obj: {'rect': (0, 0, 100, 100)},
        native_transforms=lambda obj: [])
    reader.snapshot = lambda: {'open': True, 'scope': 'scope', 'stove_identity': [1, 2, 3]}
    reader._available = lambda panel: [45101]
    monkeypatch.setattr(driver, 'rect_to_client', lambda *a: (0, 0, 10, 10))
    monkeypatch.setattr(driver.ItemNameTable, '_pairs', staticmethod(lambda *a, **k: ({}, lambda: True)))
    return reader


def test_card_recycled_during_scan_becomes_retryable_read_issue(monkeypatch):
    reader = make_reader(monkeypatch)
    with pytest.raises(SceneReadIssue) as caught:
        reader.selection((1280, 720))
    assert caught.value.kind == 'invalid_reference'
    assert caught.value.stage == 'card_scan'
    assert str(caught.value) == '料理卡片正在回收或更新'


class Clock:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0., seconds)


def test_select_recipe_retries_the_whole_read_then_succeeds():
    recipe = {'recipe_id': 45101, 'product_id': 45101, 'name': '田园沙拉',
              'status': 'confirmed', 'cooker_type': 'pot', 'ingredients': [
                  {'name': '番茄', 'quantity': 1, 'allowed_item_ids': [1]}]}
    stock = {'scope': 'bag', 'counts': {1: 5}, 'selectable_counts': {1: 5}}
    value = {'open': True, 'scope': 'scope', 'stove_identity': [1, 2, 3],
             'available_ids': [45101], 'cards': [],
             'selected': {'recipe_id': 45101, 'name': '田园沙拉'},
             'signature': 'sig',
             'selection_evidence': {'ui_manager': 1, 'panel_address': 2,
                                    'panel_active': True, 'panel_focused': True}}
    calls = []

    class Reader:
        def selection(self, size):
            calls.append(size)
            if len(calls) <= 2:
                raise SceneReadIssue('料理卡片正在回收或更新',
                                     kind='invalid_reference', stage='card_scan')
            return value

    reports = []
    releases = []

    class Controls:
        def snapshot(self):
            return True, False

        def pause(self):
            pass

    window = SimpleNamespace(size=lambda: (1280, 720), foreground=lambda: True,
                             pointer=lambda: (0, 0, True))
    device = SimpleNamespace(release_all=lambda: releases.append(True))

    result = select_recipe(recipe=recipe, reader=Reader(), window=window, controls=Controls(),
                           device=device, inventory=lambda: stock, clock=Clock(),
                           report=lambda state, message, **extra: reports.append((state, message, extra)))

    assert result['recipe_id'] == 45101
    assert len(calls) == 4  # 两次失败 + 恢复读取 + 确认读取
    # 两次读到回收各松开一次，结束时 finally 还会再松开一次。
    assert releases == [True, True, True]
    states = [state for state, _, _ in reports]
    assert 'recipe_read_retry' in states
    assert 'recipe_read_recovered' in states


def test_layout_torn_read_becomes_retryable_read_issue(monkeypatch):
    """Unity 正在写布局时，两次读到的变换不一致：整份读取作废并重试。"""
    reader = make_reader(monkeypatch)

    def torn(obj):
        raise driver.MapLayoutUpdating('界面变换数值正在更新')

    reader.layout.native_transforms = torn
    with pytest.raises(SceneReadIssue) as caught:
        reader.selection((1280, 720))
    assert caught.value.kind == 'invalid_reference'
    assert caught.value.stage == 'layout_read'
    assert str(caught.value) == '界面变换数值正在更新'
