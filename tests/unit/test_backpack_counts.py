"""Resource stock reads reuse the food reader's owned, stable bag traversal."""
import pytest

from tests.unit.test_food_inventory import inventory


def test_totals_include_locked_disabled_and_all_stacks_but_never_warehouse():
    reader, _ = inventory()
    rows, stable = reader.dictionary_rows.return_value
    reader.dictionary_rows.return_value = (rows+[rows[0]], stable)
    result = reader.counts_snapshot()
    assert result['counts'][40101] == 24
    assert result['counts'][40502] == 30  # Locked still belongs to the player.
    assert result['counts'][40201] == 3
    reader.discover_names.assert_not_called()
    reader.dictionary_rows.assert_called_once_with(25)


def test_unstable_read_is_unknown_and_not_an_empty_inventory():
    reader, _ = inventory()
    reader.stable.return_value = False
    with pytest.raises(ValueError, match='发生变化'):
        reader.counts_snapshot()


def test_dictionary_resize_preserves_scope_but_player_change_does_not():
    reader, refs = inventory()
    initial = reader.counts_snapshot()['scope']
    refs[3, 'items'] = 26
    assert reader.counts_snapshot()['scope'] == initial
    refs[10, '_instance'] = refs[1, '<XDTGame.Core.IModule.Owner>k__BackingField'] = refs[2, '<XDTGame.Core.IModule.Owner>k__BackingField'] = 99
    assert reader.counts_snapshot()['scope'] != initial
