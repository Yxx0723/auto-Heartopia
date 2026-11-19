"""图鉴解锁弹窗：概率出现，确认后点掉继续；认不出来仍停批。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.drivers import cooking_first_recipe_notice as notice
from utils.flows import cooking_batch as batch


RECIPE = {
    'recipe_id': 45561, 'product_id': 45561, 'name': '灯笼草莓热可可',
    'status': 'confirmed',
    'ingredients': [{'name': 'x', 'quantity': 1, 'allowed_item_ids': [1]}],
}


@pytest.mark.parametrize('message,expected', [
    ('好棒！第一次成功做出了草莓果酱！', True),
    ('好棒！看起来比以前灯笼草莓热可可更美味！', True),
    ('好棒！第一次成功做出了菠萝酱！', True),
    ('普通对话', False),
    ('', False),
    (None, False),
])
def test_only_album_unlock_phrases_match(message, expected):
    assert notice._is_album_message(message) is expected


def test_observe_reads_only_and_rechecks_stability(monkeypatch):
    snap = (1, (), (2, 3, 4, 5, '好棒！第一次成功做出了草莓果酱！'))
    monkeypatch.setattr(notice, '_dialog_snapshot', Mock(return_value=snap))
    result = notice.observe_album_unlock_notice(SimpleNamespace(ui=object()), None)
    assert result['state'] == 'confirmed'
    assert result['message'] == '好棒！第一次成功做出了草莓果酱！'


def test_observe_is_uncertain_when_snapshot_changes(monkeypatch):
    monkeypatch.setattr(notice, '_dialog_snapshot',
                        Mock(side_effect=[(1, (), None), (2, (), None)]))
    result = notice.observe_album_unlock_notice(SimpleNamespace(ui=object()), None)
    assert result['state'] == 'uncertain'
    assert result['error'] == 'ValueError'


class Clock:
    def sleep(self, seconds):
        self.slept = getattr(self, 'slept', 0) + seconds


class Window:
    def size(self):
        return (800, 600)

    def pointer(self):
        return (400, 150, True)

    def foreground(self):
        return True


class Device:
    def __init__(self):
        self.events = []

    def execute(self, event):
        self.events.append(event)

    def release_all(self):
        self.events.append({'type': 'release_all'})


def test_dismiss_clicks_and_verifies_popup_gone(monkeypatch):
    monkeypatch.setattr(notice, 'observe_album_unlock_notice',
                        Mock(side_effect=[{'state': 'confirmed'}, {'state': 'absent'}]))
    device, clock = Device(), Clock()
    result = notice.dismiss_album_unlock_notice(
        SimpleNamespace(ui=object()), Window(), device, clock, lambda: None)
    assert result['state'] == 'dismissed' and result['attempts'] == 1
    assert [event['type'] for event in device.events] == [
        'cursor_move', 'mouse_down', 'release_all']


def test_dismiss_fails_closed_when_popup_persists(monkeypatch):
    monkeypatch.setattr(notice, 'observe_album_unlock_notice',
                        Mock(return_value={'state': 'confirmed'}))
    device = Device()
    with pytest.raises(ValueError, match='仍未消失'):
        notice.dismiss_album_unlock_notice(
            SimpleNamespace(ui=object()), Window(), device, Clock(), lambda: None)
    assert device.events.count({'type': 'release_all'}) == 3


def test_batch_continues_after_album_popup(monkeypatch, tmp_path):
    stove = (1, 2, 3)
    stock = {'counts': {45561: 0}}
    reader = Mock()
    reader.state.return_value = {'stove_identity': stove, 'scope': 's',
                                 'local_net_id': 4, 'status': 0}
    reader.snapshot.return_value = {'stove_identity': stove}
    monkeypatch.setattr(batch, 'SinglePotReader', Mock(return_value=reader))
    monkeypatch.setattr(batch, 'material_status',
                        lambda recipe, stock: {'sufficient': True})
    monkeypatch.setattr(batch, 'observe_first_recipe_notice',
                        Mock(return_value={'state': 'uncertain'}))
    monkeypatch.setattr(batch, 'observe_album_unlock_notice',
                        Mock(return_value={'state': 'confirmed',
                                           'message': '好棒！第一次成功做出了草莓果酱！'}))
    monkeypatch.setattr(batch, 'dismiss_album_unlock_notice',
                        Mock(return_value={'state': 'dismissed', 'attempts': 1}))

    def finish_pot(**kwargs):
        stock['counts'][45561] += 1
        return {**RECIPE, 'state': 'finished', 'collect_reserved': True,
                'stove_identity': stove, 'product_delta': 1,
                'confirmed_products': 1, 'pot_id': stock['counts'][45561]}

    device = Device()
    args = dict(target=2, recipe=RECIPE,
                memory=SimpleNamespace(cooking_inventory_snapshot=lambda _: stock),
                window=Window(), controls=SimpleNamespace(snapshot=lambda: (True, False)),
                device=device, clock=Clock(), report=Mock(), directory=tmp_path,
                entry_runner=lambda **kwargs: {'state': 'MENU_OPEN'},
                selection_runner=Mock(), pot_runner=finish_pot)
    result = batch.run_batch(**args)
    assert result['completed_pots'] == 2 and result['state'] == 'batch_finished'
    saved = json.loads((tmp_path / 'batch.json').read_text(encoding='utf-8-sig'))
    assert saved['pots'][0]['album_unlock']['dismissed']['state'] == 'dismissed'
    # 图鉴弹窗的点击由 dismiss 负责；批次本身只做 release_all。
    assert device.events[-1] == {'type': 'release_all'}


def test_batch_stops_when_dialog_is_not_album(monkeypatch, tmp_path):
    stove = (1, 2, 3)
    stock = {'counts': {45561: 0}}
    reader = Mock()
    reader.state.return_value = {'stove_identity': stove, 'scope': 's',
                                 'local_net_id': 4, 'status': 0}
    reader.snapshot.return_value = {'stove_identity': stove}
    monkeypatch.setattr(batch, 'SinglePotReader', Mock(return_value=reader))
    monkeypatch.setattr(batch, 'material_status',
                        lambda recipe, stock: {'sufficient': True})
    monkeypatch.setattr(batch, 'observe_first_recipe_notice',
                        Mock(return_value={'state': 'uncertain'}))
    monkeypatch.setattr(batch, 'observe_album_unlock_notice',
                        Mock(return_value={'state': 'uncertain'}))
    dismiss = Mock()
    monkeypatch.setattr(batch, 'dismiss_album_unlock_notice', dismiss)

    def finish_pot(**kwargs):
        stock['counts'][45561] += 1
        return {**RECIPE, 'state': 'finished', 'collect_reserved': True,
                'stove_identity': stove, 'product_delta': 1,
                'confirmed_products': 1, 'pot_id': stock['counts'][45561]}

    args = dict(target=2, recipe=RECIPE,
                memory=SimpleNamespace(cooking_inventory_snapshot=lambda _: stock),
                window=Window(), controls=SimpleNamespace(snapshot=lambda: (True, False)),
                device=Device(), clock=Clock(), report=Mock(), directory=tmp_path,
                entry_runner=lambda **kwargs: {'state': 'MENU_OPEN'},
                selection_runner=Mock(), pot_runner=finish_pot)
    with pytest.raises(ValueError, match='锅间底部对话状态未确认'):
        batch.run_batch(**args)
    dismiss.assert_not_called()
