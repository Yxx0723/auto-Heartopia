import struct

from tests.unit.test_food_inventory import inventory


def test_cooking_counts_do_not_treat_locked_variants_as_selectable():
    reader, _ = inventory()
    snapshot = reader.cooking_snapshot()
    assert snapshot['counts'][40502] == 30
    assert 40502 not in snapshot['usable_counts']
    assert 40201 not in snapshot['usable_counts']
    assert snapshot['usable_counts'][40101] == 12
    assert snapshot['selectable_counts'][40101] == 12
    assert snapshot['items'][0]['net_id'] == 140101
    reader.discover_names.assert_not_called()


def test_any_protected_same_id_stack_blocks_automatic_material_selection():
    reader, _ = inventory()
    rows, stable = reader.dictionary_rows.return_value
    ci, apple = rows[0]
    locked = bytearray(apple); locked[16] = 1
    reader.dictionary_rows.return_value = (rows+[(ci, bytes(locked))], stable)
    result = reader.cooking_snapshot()
    assert result['counts'][40101] == 24
    assert 40101 not in result['usable_counts']


def test_high_star_material_is_preserved_until_user_changes_limit():
    reader, _ = inventory()
    rows, stable = reader.dictionary_rows.return_value
    ci, apple = rows[0]; changed = bytearray(apple)
    struct.pack_into('<i', changed, 12, 3)
    reader.dictionary_rows.return_value = ([(ci, bytes(changed))], stable)
    assert 40101 not in reader.cooking_snapshot(1)['usable_counts']
    assert reader.cooking_snapshot(3)['usable_counts'][40101] == 12
