"""已解锁料理图鉴读取：解析、排序与一致性复核。"""
import struct

import pytest

from utils.drivers import cooking_unlocked_recipes as module


DICT_ADDR = 0x10
GROUP_ADDR = 0x20
ITEMS_ADDR = 0x30
LIST_CLASS = 0x100
ELEMENT_CLASS = 0x200
SIZE_AT = GROUP_ADDR + 16
VERSION_AT = GROUP_ADDR + 20
NAMES = {0x9001: '田园沙拉', 0x9002: '蓝莓果酱'}


def row(recipe_id, name_at, sort_order, is_new):
    return (struct.pack('<i', recipe_id) + b'\0' * 4
            + struct.pack('<Q', name_at)
            + struct.pack('<i', sort_order) + bytes([is_new]) + b'\0' * 3)


RAW = row(45105, 0x9002, 5, 0) + row(45101, 0x9001, 1, 1)


class Meta:
    def __init__(self, stamp=(1, 1, 0, 1)):
        self.stamp = stamp
        self.calls = 0

    def obj(self, address):
        if address == DICT_ADDR:
            return {'name': 'Dictionary`2', 'namespace': 'System.Collections.Generic',
                    'address': 0x101, 'fields': {}}
        if address == GROUP_ADDR:
            return {'name': 'List`1', 'namespace': 'System.Collections.Generic',
                    'address': LIST_CLASS,
                    'fields': {'_size': {'offset': 16, 'code': 8, 'flags': 0},
                               '_version': {'offset': 20, 'code': 8, 'flags': 0}}}
        if address == ELEMENT_CLASS:
            return {'name': 'CookingRecipe',
                    'namespace': 'XDTGameSystem.GameplaySystem.Cooking',
                    'size': 40}
        raise AssertionError('unexpected obj ' + hex(address))

    def field(self, cls, name):
        return {'offset': 16 if name == '_size' else 20, 'code': 8, 'flags': 0}

    def integer(self, address):
        return {SIZE_AT: 2, VERSION_AT: 7}[address]

    def ref(self, obj, name):
        if (obj, name) == (0x10, '_cookingRecipes'):
            return DICT_ADDR
        if (obj, name) == (GROUP_ADDR, '_items'):
            return ITEMS_ADDR
        raise AssertionError('unexpected ref ' + hex(obj) + name)

    def array(self, items, cap):
        assert items == ITEMS_ADDR
        return {'element': ELEMENT_CLASS}, 32

    def cls(self, address):
        assert address == ELEMENT_CLASS
        return self.obj(address)

    def data(self, address, size):
        if address == ITEMS_ADDR + 32 and size == len(RAW):
            return RAW
        raise AssertionError('unexpected data ' + hex(address) + str(size))

    def managed_string(self, pointer):
        return NAMES[pointer]

    def dictionary(self, obj, max_items=256, with_stamp=False):
        assert obj == DICT_ADDR
        self.calls += 1
        if self.calls > 1 and self.stamp != (1, 1, 0, 1):
            return ([], self.stamp) if with_stamp else []
        return ([GROUP_ADDR], self.stamp) if with_stamp else [GROUP_ADDR]


class Locator:
    def snapshot(self):
        return {}


class Memory:
    def __init__(self, meta):
        self.meta = meta
        self.locator = Locator()


class RecipeReader:
    def __init__(self, memory):
        self.meta = memory.meta

    def context(self):
        return ('scope', 'level', 0x10)


def make_reader(monkeypatch, stamp=(1, 1, 0, 1)):
    monkeypatch.setattr(module, 'CookingRecipeReader', RecipeReader)
    meta = Meta(stamp)
    return module.CookingUnlockedRecipes(Memory(meta)), meta


def test_reads_unlocked_recipes_sorted(monkeypatch):
    reader, _ = make_reader(monkeypatch)
    data = reader.snapshot()
    assert data['count'] == 2
    assert [(r['recipe_id'], r['name']) for r in data['recipes']] == [
        (45101, '田园沙拉'), (45105, '蓝莓果酱')]
    assert data['recipes'][0]['is_new'] is True
    assert data['recipes'][1]['is_new'] is False


def test_group_mutation_detected(monkeypatch):
    reader, meta = make_reader(monkeypatch)
    original = meta.data
    state = {'count': 0}

    def changed(address, size):
        state['count'] += 1
        if address == ITEMS_ADDR + 32:
            return RAW if state['count'] == 1 else b'\0' * len(RAW)
        return original(address, size)

    meta.data = changed
    with pytest.raises(ValueError):
        reader.snapshot()


def test_dictionary_mutation_detected(monkeypatch):
    reader, _ = make_reader(monkeypatch, stamp=(2, 1, 0, 1))
    with pytest.raises(ValueError):
        reader.snapshot()
