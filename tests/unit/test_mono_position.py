"""A synthetic Mono heap; tests never open the game or produce input."""
import copy
import struct

import pytest

from utils.core.coordinate_profile import validate_profile
from utils.drivers.mono_position import MonoPositionLocator


class Runtime:
    def __init__(self, shift=0):
        self.base = 0x100000
        self.next = 0x1000000 + shift
        self.blocks = {self.base: bytearray(0x500000)}
        self.classes = {}
        self.objects = {}
        self.slots = {}
        self.image = self.alloc(0x600)
        self.p64(self.image + 0x30, self.string('XDTLevelAndEntity'))
        self.assembly = self.alloc(0x80)
        self.p64(self.assembly + 0x68, self.image)
        self.node = self.alloc(16)
        self.p64(self.node, self.assembly)
        self.p64(self.base + 0x4d3880, self.node)
        instruction = b'\x48\x8b\x1d' + struct.pack('<i', 0x4d3880 - 0x29dd3c)
        self.write(self.base + 0x29dd35, instruction)
        self.domain = self.alloc(64)
        self.p64(self.base + 0x4d3878, self.domain)
        self.add_class('Vector3', 'UnityEngine', [], size=28)
        self.add_class('Hierarchy', 'XDTGame.Core', [
            ('_hierarchyObject', 16, 18), ('_parent', 32, 18), ('_position', 56, 17)], size=96)
        self.add_class('Entity', 'XDTGame.GAS.Client', [
            ('<transformComponent>k__BackingField', 48, 18),
            ('<isSelfPlayer>k__BackingField', 148, 2), ('_alived', 156, 2)], size=160)
        self.add_class('ViewComponent', 'XDTGame.GAS.Client', [
            ('_hierarchy', 104, 18), ('<entity>k__BackingField', 16, 18)], size=160)
        self.add_class('LevelEntityComponent', 'XDTLevelAndEntity.EntityView', [],
                       parent='ViewComponent', size=336)
        self.add_class('BasePlayerComponent', 'XDTLevelAndEntity.Gameplay.Component.Player', [
            ('<entity>k__BackingField', 16, 18)], size=296)
        self.add_class('LocalPlayerComponent', 'XDTLevelAndEntity.Gameplay.Component.Player', [
            ('_character', 312, 18)], parent='BasePlayerComponent', size=504)
        self.add_class('Character', 'XDTLevelAndEntity.Game.GameMode', [
            ('_character', 0, 18, 0x11), ('player', 16, 18)], size=40)
        self.character = self.make_object('Character', 40)
        self.player = self.make_object('LocalPlayerComponent', 504)
        self.entity = self.make_object('Entity', 160)
        self.transform = self.make_object('LevelEntityComponent', 336)
        self.hierarchy = self.make_object('Hierarchy', 96)
        self.storage = self.alloc(16)
        self.p64(self.slots['Character'] + 0x50 + 4 * 8, self.storage)
        self.p64(self.storage, self.character)
        self.p64(self.character + 16, self.player)
        self.p64(self.player + 312, self.character)
        self.p64(self.player + 16, self.entity)
        self.p64(self.entity + 48, self.transform)
        self.write(self.entity + 148, b'\x01')
        self.write(self.entity + 156, b'\x01')
        self.p64(self.transform + 16, self.entity)
        self.p64(self.transform + 104, self.hierarchy)
        self.p64(self.hierarchy + 16, self.transform)
        self.set_position((98.0, 21.5, -9.0))
        buckets = self.alloc(len(self.classes) * 8)
        for i, klass in enumerate(self.classes.values()):
            self.p64(buckets + i * 8, klass)
        self.write(self.image + 0x4d0 + 24, struct.pack('<IIQ',
                   len(self.classes), len(self.classes), buckets))

    def alloc(self, size):
        address = self.next
        self.next += (size + 15) // 16 * 16
        self.blocks[address] = bytearray(size)
        return address

    def write(self, address, data):
        for base, block in self.blocks.items():
            if base <= address and address + len(data) <= base + len(block):
                block[address - base:address - base + len(data)] = data
                return
        raise AssertionError(hex(address))

    def read(self, address, size):
        for base, block in self.blocks.items():
            if base <= address and address + size <= base + len(block):
                return bytes(block[address - base:address - base + size])
        return None

    def p64(self, address, value):
        self.write(address, struct.pack('<Q', value))

    def string(self, value):
        address = self.alloc(256)
        self.write(address, value.encode() + b'\0')
        return address

    def add_class(self, name, namespace, fields, parent=None, size=160):
        klass = self.alloc(0x110)
        self.classes[name] = klass
        self.write(klass + 0x1b, b'\x01')
        self.write(klass + 0x1c, struct.pack('<I', size))
        self.p64(klass + 0x30, self.classes[parent] if parent else 0)
        self.p64(klass + 0x40, self.image)
        self.p64(klass + 0x48, self.string(name))
        self.p64(klass + 0x50, self.string(namespace))
        self.write(klass + 0x5c, struct.pack('<I', 4))
        self.write(klass + 0x100, struct.pack('<I', len(fields)))
        if fields:
            table = self.alloc(len(fields) * 32)
            self.p64(klass + 0xa0, table)
            for i, field in enumerate(fields):
                fname, offset, code, *flags = field
                typ = self.alloc(16)
                self.write(typ + 8, struct.pack('<HB', flags[0] if flags else 1, code))
                if code == 17:
                    self.p64(typ, self.classes['Vector3'])
                self.write(table + i * 32, struct.pack('<QQQi', typ, self.string(fname), klass, offset))
        vt = self.alloc(0x90)
        self.p64(vt, klass)
        self.p64(vt + 16, self.domain)
        self.write(vt + 0x38, b'\x02')
        self.p64(klass + 0xd8, vt)
        self.slots[name] = vt
        return klass

    def make_object(self, name, size):
        address = self.alloc(size)
        self.p64(address, self.slots[name])
        return address

    def set_position(self, position):
        self.write(self.hierarchy + 56, struct.pack('<3f', *position))


def test_rediscovers_relocated_heap_and_follows_the_live_role_each_sample():
    for shift in (0, 0x300000):
        runtime = Runtime(shift)
        locator = MonoPositionLocator(runtime.read, runtime.base)
        sample = locator.snapshot()
        assert sample['position'] == (98., 21.5, -9.)
        assert sample['address'] == runtime.hierarchy + 56
        assert sample['locator'] == 'mono-local-player'
        runtime.set_position((102., 21.75, -8.))
        assert locator.snapshot()['position'] == (102., 21.75, -8.)
        runtime.p64(runtime.transform + 104, 0)
        with pytest.raises(ValueError):
            locator.snapshot()


@pytest.mark.parametrize('problem', ['self', 'alive', 'character', 'owner', 'parent', 'wrong_type', 'nan', 'static', 'domain'])
def test_missing_wrong_or_stale_role_never_yields_a_coordinate(problem):
    r = Runtime()
    locator = MonoPositionLocator(r.read, r.base)
    if problem == 'self':
        r.write(r.entity + 148, b'\0')
    elif problem == 'alive':
        r.write(r.entity + 156, b'\0')
    elif problem == 'character':
        r.p64(r.player + 312, r.player)
    elif problem == 'owner':
        r.p64(r.hierarchy + 16, r.entity)
    elif problem == 'parent':
        r.p64(r.hierarchy + 32, r.hierarchy)
    elif problem == 'wrong_type':
        r.p64(r.player, r.slots['Entity'])
    elif problem == 'nan':
        r.set_position((float('nan'), 2, 3))
    elif problem == 'static':
        r.p64(r.storage, 0)
    elif problem == 'domain':
        r.p64(r.slots['Character'] + 16, 0)
    with pytest.raises(ValueError):
        locator.snapshot()


def test_scene_reference_changes_during_read_are_rejected():
    r = Runtime()
    def read(address, size):
        result = r.read(address, size)
        if address == r.hierarchy + 56:
            r.p64(r.storage, 0)
        return result
    with pytest.raises(ValueError):
        MonoPositionLocator(read, r.base).snapshot()


@pytest.mark.parametrize('problem', ['code', 'cycle', 'huge_cache', 'missing_class', 'short_read'])
def test_corrupt_or_incompatible_metadata_fails_with_a_bounded_error(problem):
    r = Runtime()
    if problem == 'code':
        r.write(r.base + 0x29dd35, b'\0' * 7)
    elif problem == 'cycle':
        r.p64(r.node + 8, r.node)
    elif problem == 'huge_cache':
        r.write(r.image + 0x4d0 + 24, struct.pack('<I', 0xffffffff))
    elif problem == 'missing_class':
        r.p64(r.classes['Character'] + 0x48, r.string('Other'))
    read = r.read if problem != 'short_read' else lambda a, n: b'\0' * max(0, n - 1)
    with pytest.raises(ValueError):
        MonoPositionLocator(read, r.base).snapshot()


def semantic_profile():
    return {'schema': 'myrail-position-mono-v1', 'process_name': 'xdt.exe',
        'module': 'mono-2.0-sgen.dll', 'sha256': 'a' * 64, 'file_size': 123456,
        'unity_player_sha256': 'b' * 64, 'unity_player_file_size': 234567,
        'game_assembly_sha256': 'c' * 64, 'game_assembly_file_size': 345678,
        'read_only': True, 'verified_after_restart': True,
        'verified_player_movement': True, 'verified_camera_independence': True,
        'require_movement_calibration': True}


def test_semantic_profile_requires_the_actual_verifications_and_all_binary_fingerprints():
    profile = semantic_profile()
    assert validate_profile(profile) == []
    for key in ('verified_after_restart', 'verified_player_movement',
                'verified_camera_independence', 'read_only', 'require_movement_calibration'):
        invalid = copy.deepcopy(profile)
        invalid[key] = False
        assert validate_profile(invalid), key
    for key in ('sha256', 'unity_player_sha256', 'game_assembly_sha256'):
        invalid = copy.deepcopy(profile)
        invalid[key] = 'invalid'
        assert validate_profile(invalid), key


def test_parser_budgets_are_pinned_to_the_documented_read_only_limits():
    from utils.core import thresholds
    assert (thresholds.MONO_ASSEMBLY_LIMIT, thresholds.MONO_CLASS_LIMIT,
            thresholds.MONO_PARENT_LIMIT, thresholds.MONO_FIELD_LIMIT,
            thresholds.MONO_VTABLE_LIMIT, thresholds.MONO_OBJECT_LIMIT,
            thresholds.MONO_NAME_BYTES) == (512, 100000, 16, 512, 4096, 65536, 256)


def test_memory_adapter_selects_semantic_runtime_and_checks_all_three_binary_versions():
    from pathlib import Path
    from utils.drivers.coordinate_memory import ReadOnlyPosition
    from unittest.mock import Mock
    r = Runtime()
    reader = ReadOnlyPosition.__new__(ReadOnlyPosition)
    reader.profile = semantic_profile()
    reader._module = Mock(side_effect=lambda name: (r.base, 0x500000, Path(name)))
    reader._fingerprint = Mock()
    reader._read = r.read
    reader.configure_locator()
    assert reader._module.call_args_list[0].args == ('mono-2.0-sgen.dll',)
    assert [call.args[0].name for call in reader._fingerprint.call_args_list] == [
        'mono-2.0-sgen.dll', 'UnityPlayer.dll', 'GameAssembly.dll']
    reader.hwnd = None
    assert reader.snapshot()['position'] == (98., 21.5, -9.)
