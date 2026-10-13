import struct

import pytest

from utils.core.coordinate_profile import validate_profile
from utils.drivers.coordinate_memory import consensus_position, position_snapshot


def test_unreadable_or_conflicting_pointer_chains_never_produce_a_position():
    paths = [{'rva': 0x10, 'offsets': [0x20]}, {'rva': 0x18, 'offsets': [0x20]}]
    data = {0x100010: struct.pack('<Q', 0x200000), 0x100018: struct.pack('<Q', 0x300000),
            0x200020: struct.pack('<fff', 1, 2, 3), 0x300020: struct.pack('<fff', 1, 2, 3)}
    read = lambda a, n: data.get(a)
    assert consensus_position(read, 0x100000, paths) == (1, 2, 3)
    data[0x300020] = struct.pack('<fff', 100, 2, 3)
    with pytest.raises(ValueError, match='不一致'):
        consensus_position(read, 0x100000, paths)
    del data[0x300020]
    with pytest.raises(ValueError):
        consensus_position(read, 0x100000, paths)


def test_bad_pointer_and_nan_are_missing_not_zero_coordinates():
    paths = [{'rva': 0, 'offsets': [0]}, {'rva': 8, 'offsets': [0]}]
    data = {0x100000: struct.pack('<Q', 0), 0x100008: struct.pack('<Q', 0x200000),
            0x200000: struct.pack('<fff', float('nan'), 2, 3)}
    with pytest.raises(ValueError):
        consensus_position(lambda a, n: data.get(a), 0x100000, paths)


def test_profile_rejects_unverified_and_out_of_range_offsets():
    profile = {'schema': 'myrail-position-v1', 'process_name': 'xdt.exe', 'module': 'UnityPlayer.dll',
               'sha256': 'a' * 64, 'file_size': 100000, 'read_only': True, 'verified_after_restart': True,
               'game_assembly_sha256': 'b' * 64, 'game_assembly_file_size': 100000,
               'paths': [{'rva': 16, 'offsets': [32]}, {'rva': 24, 'offsets': [32]}]}
    assert validate_profile(profile) == []
    profile['paths'][0]['offsets'][0] = -1
    assert validate_profile(profile)
    profile['paths'][0]['offsets'][0] = 32
    profile['verified_after_restart'] = False
    assert validate_profile(profile)


def test_snapshot_counts_only_readable_paths_and_reuses_shared_xyz():
    paths = [{'rva': 0, 'offsets': [0]}, {'rva': 8, 'offsets': [0]},
             {'rva': 16, 'offsets': [0]}]
    data = {0x100000: struct.pack('<Q', 0x200000), 0x100008: struct.pack('<Q', 0x200000),
            0x100010: struct.pack('<Q', 0), 0x200000: struct.pack('<fff', 1, 2, 3)}
    calls = []
    def read(address, size):
        calls.append((address, size))
        return data.get(address)
    snapshot = position_snapshot(read, 0x100000, paths)
    assert snapshot['position'] == (1., 2., 3.)
    assert snapshot['valid_paths'] == 2
    assert snapshot['configured_paths'] == 3
    assert snapshot['address'] == 0x200000
    assert snapshot['module_base'] == 0x100000
    assert snapshot['paths'][0]['root_address'] == 0x100000
    assert snapshot['paths'][0]['trace'] == [{'read_at': 0x100000, 'pointer': 0x200000, 'offset': 0}]
    assert calls.count((0x200000, 12)) == 1
