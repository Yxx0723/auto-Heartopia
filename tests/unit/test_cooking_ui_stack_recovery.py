"""Synthetic memory and production P1 flow; never creates a real input device."""
import pytest

from test_cooking_menu_state import reader
from test_cooking_recipe_switch import Kitchen, run, left_clicks
from utils.drivers.cooking_menu_state import CookingMenuStateChanged
from utils.core.scene_read import SceneReadIssue


def changed_reader(kind='ui'):
    r, refs, fields = reader()
    original = r.ui.meta.obj
    r.ui.meta.obj = lambda obj: ({'name': 'ToastPanel', 'namespace': 'XDTGame.UI.Panel'}
                                if obj == 99 else original(obj))
    before = (1, 2, 3, (10,))
    after = (1, 2, 3, (10, 99))
    r.ui.ui_views.side_effect = [before, after if kind in ('ui', 'both') else before]
    r.recipe.context.side_effect = [(1, 2, 3), (1, 2, 4) if kind in ('context', 'both') else (1, 2, 3)]
    return r, refs, fields


@pytest.mark.parametrize('kind,classification,recoverable', [
    ('ui', 'UI_STACK_CHANGED', True), ('context', 'COOKING_CONTEXT_CHANGED', False),
    ('both', 'BOTH_CHANGED', False)])
def test_sources_are_compared_independently(kind, classification, recoverable):
    r, _, _ = changed_reader(kind)
    with pytest.raises(CookingMenuStateChanged) as caught:
        r.snapshot()
    exc = caught.value
    assert exc.classification == classification and exc.recoverable is recoverable
    assert r.ui.ui_views.call_count == r.recipe.context.call_count == 2
    d = exc.diagnostics
    assert d['before']['ui'] == (1, 2, 3, (10,))
    assert d['before']['context'] == (1, 2, 3)
    assert d['ui_changed'] == (kind in ('ui', 'both'))
    assert d['context_changed'] == (kind in ('context', 'both'))
    if kind == 'ui':
        assert list(d['changed_fields']['ui']) == ['views']
        assert d['stove_identity_after'] == d['stove_identity'] == [370033, 7, 8]


def test_stable_sources_return_original_open_snapshot():
    r, _, _ = reader()
    assert r.snapshot()['open']


@pytest.mark.parametrize('stage', ['before', 'after'])
def test_read_failures_keep_both_source_errors_without_short_circuit(stage):
    r, _, _ = reader()
    count = 0 if stage == 'before' else 1
    r.ui.ui_views.side_effect = [(1, 2, 3, (10,))] * count + [OSError('UI read')]
    r.recipe.context.side_effect = [(1, 2, 3)] * count + [RuntimeError('context read')]
    with pytest.raises(CookingMenuStateChanged) as caught:
        r.snapshot()
    d = caught.value.diagnostics
    assert d['classification'] == 'READ_FAILED' and not d['recoverable']
    assert d[stage + '_errors']['ui']['type'] == 'OSError'
    assert d[stage + '_errors']['context']['type'] == 'RuntimeError'
    assert r.ui.ui_views.call_count == r.recipe.context.call_count == count + 1


def test_ui_manager_replacement_is_not_recoverable():
    r, _, _ = changed_reader()
    r.ui.ui_views.side_effect = [(1, 2, 3, (10,)), (1, 200, 3, (10,))]
    with pytest.raises(CookingMenuStateChanged) as caught:
        r.snapshot()
    assert not caught.value.recoverable


@pytest.mark.parametrize('index,field', [(0, 'current_level_node'), (1, 'level_instance'),
                                       (2, 'cooking_system')])
def test_each_cooking_context_identity_change_stops(index, field):
    r, _, _ = changed_reader('context')
    after = [1, 2, 3]
    after[index] = 900
    r.recipe.context.side_effect = [(1, 2, 3), tuple(after)]
    with pytest.raises(CookingMenuStateChanged) as caught:
        r.snapshot()
    assert not caught.value.recoverable
    assert list(caught.value.diagnostics['changed_fields']['context']) == [field]


def test_stove_changes_after_ui_comparison_are_not_recoverable():
    r, _, fields = changed_reader()
    def context():
        if r.ui.ui_views.call_count == 2:
            fields['_cookerNetId'] = b'\x09\x00\x00\x00'
        return (1, 2, 3)
    r.recipe.context.side_effect = context
    with pytest.raises(CookingMenuStateChanged) as caught:
        r.snapshot()
    assert not caught.value.recoverable
    assert caught.value.diagnostics['stove_identity_after'] == [370033, 9, 8]


def ui_issue(kind='UI_STACK_CHANGED', recoverable=True):
    return CookingMenuStateChanged({'classification': kind, 'recoverable': recoverable,
                                   'scope': 'S', 'stove_identity': [370033, 7, 8]})


class RefreshKitchen(Kitchen):
    def __init__(self, mode='recover', *, after_click=False):
        super().__init__('recover', mismatch_reads=0)
        self.refresh_mode = mode
        self.after_click = after_click
        self.refresh_reads = 0
        self.calls = 0
        self.stopped = False
        self.recovered_calls = []

    def snapshot(self):
        return (True, self.stopped)

    def selection(self, size):
        self.calls += 1
        active = self.clicked if self.after_click else True
        if active:
            self.refresh_reads += 1
            if self.refresh_reads == 1 or self.refresh_mode == 'exhausted':
                if self.refresh_mode == 'deadline':
                    self.now = 1.
                if self.refresh_mode == 'stop':
                    self.stopped = True
                if self.refresh_mode in ('context', 'both'):
                    raise ui_issue('COOKING_CONTEXT_CHANGED' if self.refresh_mode == 'context' else 'BOTH_CHANGED', False)
                raise ui_issue()
            if self.refresh_reads == 2:
                if self.refresh_mode == 'invalid':
                    raise OSError('replacement object unreadable')
                if self.refresh_mode == 'invalid_reference':
                    raise SceneReadIssue('replacement object invalid', kind='invalid_reference')
        data = super().selection(size)
        if active and self.refresh_mode == 'scene': data['scope'] = 'OTHER'
        if active and self.refresh_mode == 'stove': data['stove_identity'] = [370033, 9, 8]
        if active and self.refresh_mode == 'wrong_recipe':
            data['selected'] = {'recipe_id': 45557, 'name': '灯笼苹果热可可'}
        if active and self.refresh_mode == 'wrong_name':
            data['selected'] = {'recipe_id': 45558, 'name': '错误名称'}
        return data

    def report(self, category, message, **kwargs):
        super().report(category, message, **kwargs)
        if category == 'recipe_snapshot_recovered': self.recovered_calls.append(self.refresh_reads)


@pytest.mark.parametrize('after_click', [False, True])
def test_refresh_recovers_using_fresh_data_without_reclick(monkeypatch, after_click):
    k = RefreshKitchen(after_click=after_click)
    assert run(k, monkeypatch)['recipe_id'] == 45558
    assert left_clicks(k) == 1
    assert k.recovered_calls == [2]
    assert not any(e['type'].startswith('key') for e in k.events)
    assert any(category == 'RECIPE_SELECTED' for category, _ in k.reports)


@pytest.mark.parametrize('mode', ['exhausted', 'context', 'both', 'scene', 'stove',
                                 'invalid', 'invalid_reference', 'stop', 'deadline', 'wrong_recipe', 'wrong_name'])
def test_unsafe_or_unconfirmed_refresh_stops_without_recipe_success(monkeypatch, mode):
    k = RefreshKitchen(mode)
    with pytest.raises(ValueError) as caught:
        run(k, monkeypatch, timeout=1.)
    assert left_clicks(k) <= 1
    assert not any(e['type'].startswith('key') for e in k.events)
    assert not any(category == 'RECIPE_SELECTED' for category, _ in k.reports)
    assert k.now <= 1.01
    if mode == 'exhausted':
        assert k.calls == 3
        assert caught.value.diagnostics['stop_reason'] == 'UI 只读读取已达 3 次上限'
    if mode in ('context', 'both'): assert k.calls == 1
    if mode in ('invalid', 'invalid_reference'):
        assert k.calls == 2
        assert caught.value.classification == 'READ_FAILED'
        assert isinstance(caught.value.__cause__, OSError if mode == 'invalid' else SceneReadIssue)
    if mode == 'stop':
        assert k.calls == 1 and '选菜已停止' in str(caught.value)
    if mode == 'deadline':
        assert k.calls == 1 and '原选菜截止时间' in str(caught.value)
