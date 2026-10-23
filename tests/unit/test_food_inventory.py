"""Ownership, edible-action filtering and mutable backpack snapshot checks."""
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.drivers.food_inventory import FoodInventoryReader, NS, OWNER, KNOWN_NAMES


def inventory():
    reader = FoodInventoryReader.__new__(FoodInventoryReader)
    names = {1: ('BagModule', 'XDTLevelAndEntity.Game.Module.Bag'),
             2: ('BackPackSystem', NS), 3: ('StorageBackpack', NS), 4: ('StoreWarehouse', NS)}
    refs = {(10, '_instance'): 20, (10, '_viewModules'): 21,
            (21, 'gameLevel'): 10, (21, 'group'): 22, (22, '_modules'): 23,
            (1, 'BackPackSystem'): 2, (1, OWNER): 20, (2, OWNER): 20,
            (2, '_storages'): 24, (3, 'items'): 25}
    meta = SimpleNamespace(domain=100, static_ref=lambda *args: 10,
                           ref=lambda obj, key: refs[(obj, key)],
                           obj=lambda obj: dict(zip(('name', 'namespace'), names[obj])),
                           array_refs=lambda _: [1], dictionary=lambda *args, **kw: [3, 4],
                           pointer=lambda at: 100)
    reader.meta, reader.world_class = meta, 1000
    reader.memory = SimpleNamespace(base=0x100000, locator=SimpleNamespace(snapshot=Mock()))
    reader.names = dict(KNOWN_NAMES)
    reader.discover_names = Mock()
    fields = {'staticId': 16, 'netId': 20, 'count': 24, '_starRate': 28,
              'isLock': 32, 'IsDisable': 33, 'entityType': 36, 'functions': 40}
    reader.offset = lambda ci, name, *args: fields[name]
    def row(item_id, count, locked=0, disabled=0, actions=1):
        data = bytearray(32)
        struct.pack_into('<iiii', data, 0, item_id, item_id+100000, count, 0)
        data[16], data[17] = locked, disabled
        struct.pack_into('<i', data, 20, 45 if item_id == 45420 else 25)
        struct.pack_into('<Q', data, 24, actions)
        return ({}, bytes(data))
    rows = [row(40101, 12), row(40501, 0), row(40502, 30, locked=1),
            row(40201, 3, disabled=1), row(48002, 5, actions=2), row(45420, 2)]
    reader.stable = Mock(return_value=True)
    reader.dictionary_rows = Mock(return_value=(rows, reader.stable))
    reader.actions = lambda obj: (112,) if obj == 1 else (0, 1, 3)
    return reader, refs


def test_only_current_backpack_owned_unlocked_usable_eat_actions_are_candidates():
    reader, _ = inventory()
    foods = reader.snapshot()
    assert [(f['name'], f['count']) for f in foods] == [('苹果', 12), ('温泉蛋', 2)]
    reader.dictionary_rows.assert_called_once_with(25)  # Not warehouse storage.
    reader.memory.locator.snapshot.assert_called_once()
    assert all(f['edible'] is True for f in foods)
    assert [f['prepared'] for f in foods] == [False, True]


@pytest.mark.parametrize('reference', [(1, OWNER), (2, OWNER), (21, 'gameLevel')])
def test_foreign_player_or_scene_inventory_is_rejected(reference):
    reader, refs = inventory()
    refs[reference] = 999
    with pytest.raises(ValueError, match='归属|属于'):
        reader.snapshot()


def test_inventory_changes_during_read_are_retried_instead_of_publishing_stale_food():
    reader, _ = inventory()
    reader.stable.return_value = False
    with pytest.raises(ValueError, match='发生变化'):
        reader.snapshot()


def test_locked_variant_marks_only_its_own_quality_as_ambiguous():
    reader, _ = inventory()
    rows, stable = reader.dictionary_rows.return_value
    item_type, apple = rows[0]
    locked = bytearray(apple)
    locked[16] = 1
    reader.dictionary_rows.return_value = ([*rows, (item_type, bytes(locked))], stable)
    foods = reader.snapshot()
    assert [f['name'] for f in foods] == ['苹果','温泉蛋']
    assert foods[0]['ambiguous_locked_variant'] is True
    assert foods[1]['ambiguous_locked_variant'] is False


def test_known_name_catalog_does_not_scan_memory_or_establish_ownership():
    reader = FoodInventoryReader.__new__(FoodInventoryReader)
    reader.names = dict(KNOWN_NAMES)
    reader.discover_names({40101, 40502}, Mock())  # No memory/context is available.


def test_duplicate_backpack_or_missing_backpack_is_not_a_usable_inventory():
    for stores in ([4], [3, 3, 4]):
        reader, _ = inventory()
        reader.meta.dictionary = lambda *args, **kw: stores
        with pytest.raises(ValueError, match='背包尚未就绪'):
            reader.snapshot()


def test_locked_other_star_does_not_remove_owned_unlocked_dish():
    reader,_=inventory()
    rows,stable=reader.dictionary_rows.return_value
    ci,egg=rows[-1]
    protected=bytearray(egg);protected[16]=1
    struct.pack_into('<i',protected,12,1)
    struct.pack_into('<I',protected,4,999999)
    reader.dictionary_rows.return_value=([*rows,(ci,bytes(protected))],stable)
    egg=next(i for i in reader.snapshot() if i['item_id']==45420)
    assert egg['star']==0 and egg['verify_star'] is True
    assert egg['ambiguous_locked_variant'] is False
