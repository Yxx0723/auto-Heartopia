"""Owned singleton/detail reads, torn snapshots and layout failures; no game input."""
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.drivers.cooking_recipe import CookingRecipeReader, NS
from utils.drivers.food_inventory import OWNER


def reader():
    r = CookingRecipeReader.__new__(CookingRecipeReader)
    fields = {'staticId': (40, 8), 'name': (16, 14), 'materialSlots': (32, 29),
              'filled': (28, 2), 'filledMaterialNetId': (32, 9), 'filledMaterialStaticId': (36, 8)}
    infos = {100: {'name': 'CookingSystem', 'namespace': NS, 'address': 1, 'size': 64},
             200: {'name': 'CookingRecipeDetail', 'namespace': NS, 'address': 2, 'size': 64},
             400: {'name': 'MaterialSlot', 'namespace': NS, 'address': 3, 'size': 44}}
    raw = {200: bytearray(48), 400: bytearray(28)}
    struct.pack_into('<Q', raw[200], 0, 500); struct.pack_into('<Q', raw[200], 16, 300)
    struct.pack_into('<i', raw[200], 24, 55)
    raw[400][12] = 1; struct.pack_into('<Ii', raw[400], 16, 7, 10)
    refs = {(10, '_instance'): 20, (100, OWNER): 20, (100, '_recipeDetail'): 200}
    def offset(ci, name, code, width):
        off, actual = fields[name]
        if actual != code: raise ValueError('字段布局不符')
        return off
    r.klass = {'parent': 11}
    r.food = SimpleNamespace(world_class=9, offset=offset)
    r.memory = SimpleNamespace(base=0, locator=SimpleNamespace(snapshot=Mock()))
    r.meta = SimpleNamespace(domain=99, pointer=lambda at: 99,
        static_ref=lambda ci, field: 10 if ci == 9 else 100,
        ref=lambda obj, field: refs[(obj, field)], obj=lambda obj: infos[obj],
        data=lambda at, size: bytes(raw[at-16])[:size], managed_string=lambda at: '料理',
        array=lambda at, limit: ({}, 1), array_refs=lambda at: [400])
    return r, refs, raw, infos, fields


def test_current_owned_detail_and_slots_have_repeatable_signature():
    r, *_ = reader()
    a, b = r.snapshot(), r.snapshot()
    assert a == b and a['scope'] and a['signature']
    assert a['selected'] == {'recipe_id': 55, 'name': '料理',
                             'slots': [{'filled': True, 'net_id': 7, 'item_id': 10}]}


@pytest.mark.parametrize('fault', ['owner', 'domain', 'layout', 'type', 'filled', 'mutating', 'array'])
def test_foreign_layout_and_torn_recipe_snapshots_are_not_authorization(fault):
    r, refs, raw, infos, fields = reader()
    if fault == 'owner': refs[(100, OWNER)] = 999
    if fault == 'domain': r.meta.pointer = lambda at: 999
    if fault == 'layout': fields['staticId'] = (40, 9)
    if fault == 'type': infos[400]['namespace'] = 'other'
    if fault == 'filled': raw[400][12] = 2
    if fault == 'array': r.meta.array_refs = Mock(side_effect=[[400], [401]])
    if fault == 'mutating':
        original = r.meta.data
        def data(at, size):
            result = original(at, size)
            if at == 416: raw[400][12] ^= 1
            return result
        r.meta.data = data
    with pytest.raises(ValueError): r.snapshot()


def test_absent_detail_is_explicit_unknown_and_scene_change_is_rejected():
    r, refs, *_ = reader(); refs[(100, '_recipeDetail')] = 0
    assert r.snapshot()['selected'] is None
    r.context = Mock(side_effect=[(10, 20, 100), (11, 21, 100)])
    with pytest.raises(ValueError, match='切换'): r.snapshot()
