"""Role ownership and coherent snapshots with synthetic ECS metadata."""
import struct
from types import SimpleNamespace

import pytest

from utils.drivers.stamina import StaminaReader


def reader(monkeypatch, foreign=False, mutate=False, switched=False):
    entity, ugc, actor, world, ecs, unit, poolarray = 100, 200, 300, 400, 500, 600, 700
    pools = [0, 801, 802, 803]
    ids = {'EntityData': 1, 'LocalPlayerComponentData': 2, 'PlayerPropertyData': 3}
    identity = bytes(range(32))
    record = bytearray(96)
    struct.pack_into('<8H', record, 0, 1, 2, 3, 0, 0, 0, 0, 0)
    struct.pack_into('<8i', record, 16, 0, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into('<iiQ', record, 48, 3, 0, 0)
    struct.pack_into('<I', record, 64, 1)
    refs = {(entity, '_components'): 101, (ugc, '<actor>k__BackingField'): actor,
            (actor, '_renderEntity'): entity, (entity, '_entityWorld'): world,
            (world, '<main>k__BackingField'): entity, (world, '_clientEcsWorld'): ecs,
            (ecs, '_ecsWorld'): unit, (unit, 'ComponentPools'): poolarray}
    classes = {ugc: {'name': 'UgcComponent', 'namespace': 'XDTGame.GAS', 'address': 201},
               actor: {'name': 'UgcEntity', 'namespace': 'XDTGame.GAS', 'address': 301},
               unit: {'name': 'UnitEcsWorld', 'namespace': 'XDGame.Ecs', 'address': 601}}
    calls = [0, 0]
    def sample():
        calls[0] += 1
        return {'paths': [{'trace': [{'field': '<entity>k__BackingField',
                                     'pointer': 999 if switched and calls[0] > 1 else entity}]}]}
    def data(at, size):
        if at == 316: return identity
        if at == 10000: return b'X'*32 if foreign else identity
        if at == 11000:
            calls[1] += 1
            return struct.pack('<5i', 100, 26 if mutate and calls[1] > 1 else 16, 100, 100, 100)
        if at == 13000: return bytes(record)
        raise AssertionError((at, size))
    def cls(at):
        return {1001: {'name': 'EntityData', 'size': 48},
                1002: {'name': 'EcsEntityData', 'size': 112}}[at]
    def field(at, name):
        if name == '_entityData': return {'code': 17, 'type': 1001, 'offset': 16}
        if name == '_entities': return {'code': 21, 'type': 2000, 'offset': 96, 'flags': 1}
        return {'code': 8, 'offset': {'BaseMaxStamina': 16, 'CurrStamina': 20, 'BoostedMaxValue': 24}[name], 'flags': 6}
    meta = SimpleNamespace(data=data, obj=lambda at: classes[at], cls=cls, field=field,
                           ref=lambda at, n: refs[at, n], dictionary=lambda *a, **kw: [ugc],
                           array_refs=lambda at: pools, pointer=lambda at: {2008: 2100, 2108: 2200, 2200: 1002}[at])
    key = (entity, actor, unit, poolarray, tuple(pools))
    value = StaminaReader.__new__(StaminaReader)
    value.meta, value.locator = meta, SimpleNamespace(snapshot=sample)
    value.pool_cache, value.record_hint = (key, ids), None
    def native_pool(pool, name):
        address = {'EntityData': 10000, 'LocalPlayerComponentData': 12000, 'PlayerPropertyData': 11000}[name]
        return SimpleNamespace(at=lambda index: address, count=1, stable=lambda: True), {'size': 36, 'address': 5000}
    value.native_pool = native_pool
    monkeypatch.setattr('utils.drivers.stamina.NativeGrowList',
                        lambda *args: SimpleNamespace(at=lambda index: 13000, count=1, stable=lambda: True))
    return value


def test_only_current_role_property_is_read_and_record_hint_is_revalidated(monkeypatch):
    value = reader(monkeypatch)
    assert value.snapshot()['current'] == 16
    assert value.record_hint == 0
    assert value.snapshot()['maximum'] == 100


@pytest.mark.parametrize('fault', ['foreign', 'mutate', 'switched'])
def test_other_players_or_mixed_samples_cannot_publish_stamina(monkeypatch, fault):
    value = reader(monkeypatch, **{fault: True})
    with pytest.raises(ValueError):
        value.snapshot()
