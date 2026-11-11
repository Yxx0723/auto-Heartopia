import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.drivers import cooking_first_recipe_notice as notice
from utils.flows import cooking_batch


@pytest.mark.parametrize('recipe_id,message,expected', [
    (45164, '好棒！第一次成功做出了菠萝酱！', 'confirmed'),
    (1, '好棒！第一次成功做出了菠萝酱！', 'uncertain'),
    (45164, '普通对话', 'uncertain'),
    (45164, '', 'uncertain'),
])
def test_exact_message_and_recipe(monkeypatch, recipe_id, message, expected):
    monkeypatch.setattr(notice, '_dialog_snapshot', lambda ui: (1, (), (2, 3, 4, 5, message)))
    button = Mock(side_effect=ValueError('没有可验证的按钮区域'))
    monkeypatch.setattr(notice, '_button_evidence', button)
    result = notice.observe_first_recipe_notice(SimpleNamespace(ui=object()), None,
                                               {'recipe_id': recipe_id})
    assert result['state'] == expected
    assert result['auto_click_enabled'] is False
    assert button.call_count == int(expected == 'confirmed')


def test_changed_snapshot_is_uncertain(monkeypatch):
    snapshot = Mock(side_effect=[(1, (), None), (2, (), None)])
    monkeypatch.setattr(notice, '_dialog_snapshot', snapshot)
    result = notice.observe_first_recipe_notice(SimpleNamespace(ui=object()), None,
                                               {'recipe_id': 45164})
    assert result['state'] == 'uncertain'


@pytest.mark.parametrize('state,completed', [('absent', 2), ('confirmed', 1), ('uncertain', 1)])
def test_batch_records_completed_pot_before_notice(monkeypatch, tmp_path, state, completed):
    recipe = {'recipe_id': 45164, 'product_id': 45164}
    stove = (1, 2, 3)
    stock = {'counts': {45164: 0}}
    reader = Mock()
    reader.state.return_value = {'stove_identity': stove, 'scope': 's', 'local_net_id': 4, 'status': 0}
    reader.snapshot.return_value = {'stove_identity': stove}
    monkeypatch.setattr(cooking_batch, 'SinglePotReader', lambda memory: reader)
    monkeypatch.setattr(cooking_batch, 'material_status', lambda recipe, stock: {'sufficient': True})
    detection = Mock(return_value={'state': state, 'auto_click_enabled': False})
    monkeypatch.setattr(cooking_batch, 'observe_first_recipe_notice', detection)

    def finish_pot(**kwargs):
        stock['counts'][45164] += 1
        return {**recipe, 'state': 'finished', 'collect_reserved': True, 'stove_identity': stove,
                'product_delta': 1, 'confirmed_products': 1, 'pot_id': stock['counts'][45164]}

    window, controls, device = Mock(), Mock(), Mock()
    window.size.return_value = (800, 600)
    window.foreground.return_value = True
    controls.snapshot.return_value = (True, False)
    args = dict(target=2, recipe=recipe, memory=SimpleNamespace(cooking_inventory_snapshot=lambda _: stock),
                window=window, controls=controls, device=device, clock=None, report=Mock(),
                directory=tmp_path, entry_runner=lambda **kwargs: {'state': 'MENU_OPEN'},
                selection_runner=Mock(), pot_runner=finish_pot)
    if state == 'absent':
        cooking_batch.run_batch(**args)
    else:
        with pytest.raises(ValueError):
            cooking_batch.run_batch(**args)
    saved = json.loads((tmp_path / 'batch.json').read_text(encoding='utf-8-sig'))
    assert saved['completed_pots'] == saved['total_products'] == completed
    assert saved['pots'][0]['first_recipe_notice']['state'] == state
    assert detection.call_count == 1
    assert [call[0] for call in device.method_calls] == ['release_all']
