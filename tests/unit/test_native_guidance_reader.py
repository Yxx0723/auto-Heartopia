"""Typed and mutation-aware reads without opening any process."""
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.drivers.native_guidance import NativeGuidanceReader


class Metadata:
    def __init__(self):
        self.memory = bytearray(1000)
        self.memory[116] = 1
        self.memory[120:132] = struct.pack('<3f', 0, 0, 8)
        self.fields = {'module': {'isNavigating': (16, 2), 'end': (20, 17)},
                       'list': {'_size': (16, 8), '_version': (20, 8)}}
        self.references = {(100, 'realPathList'): 200, (100, '_path'): 300,
                           (200, '_items'): 400, (300, '_items'): 500}
        for at in (200, 300):
            self.memory[at+16:at+24] = struct.pack('<2i', 2, 1)
        for at in (400, 500):
            self.memory[at+32:at+56] = struct.pack('<6f', 0, .5, 0, 0, .5, 4)
        self.hook = None
        self.element_name = 'Vector3'

    def obj(self, obj):
        return ({'address': 'module', 'name': 'TrackingPathModule', 'size': 64} if obj == 100 else
                {'address': 'list', 'name': 'List`1', 'namespace': 'System.Collections.Generic', 'size': 32})

    def field(self, cls, name):
        offset, code = self.fields[cls][name]
        return {'offset': offset, 'code': code, 'flags': 0}

    def data(self, at, size):
        value = bytes(self.memory[at:at+size])
        if self.hook:self.hook(at, size)
        return value

    def integer(self, at):return struct.unpack('<i', self.data(at, 4))[0]
    def ref(self, obj, name):return self.references[obj, name]
    def array(self, obj):return {'element': 'vector'}, 2
    def cls(self, cls):return {'name': self.element_name, 'namespace': 'UnityEngine', 'size': 28}


def reader():
    value = NativeGuidanceReader.__new__(NativeGuidanceReader)
    value.meta = Metadata()
    value.memory = SimpleNamespace(profile={'game_assembly_sha256': 'a'*64}, pid=7,
                                   locator=SimpleNamespace(domain=9))
    value.clock = lambda: 1.
    value._owner = Mock(return_value=(10, 20, 100))
    return value


def test_snapshot_uses_owned_lists_and_does_not_subtract_display_height():
    value = reader().snapshot()
    assert value['state'] == 'ready' and value['target'] == (0, 0, 8)
    assert value['path'] == ((0, .5, 0), (0, .5, 4))
    assert value['captured_at'] == 1. and len(value['context']) == 64


@pytest.mark.parametrize('fault', ['version', 'owner', 'target', 'type', 'oversized', 'field_bounds'])
def test_mutations_or_invalid_types_never_publish_ready_path(fault):
    r = reader();m = r.meta
    if fault == 'version':
        def hook(at, size):
            if at == 432:m.memory[220:224] = struct.pack('<i', 2)
        m.hook = hook
    elif fault == 'owner':r._owner = Mock(side_effect=[(10,20,100), (11,20,100)])
    elif fault == 'target':
        def hook(at, size):
            if at == 432:m.memory[120:132] = struct.pack('<3f', 0,0,9)
        m.hook = hook
    elif fault == 'type':m.element_name = 'OtherVector'
    elif fault == 'oversized':m.memory[216:220] = struct.pack('<i', 999999)
    elif fault == 'field_bounds':m.fields['module']['end'] = (9999, 17)
    with pytest.raises(ValueError):r.snapshot()


def test_display_line_endpoint_gap_is_reported_not_mistaken_for_corruption():
    r = reader();r.meta.memory[120:132] = struct.pack('<3f', 500,0,500)
    value = r.snapshot()
    assert value['state'] == 'ready' and value['target_gap_xz'] > 100


def test_inactive_guidance_does_not_read_unset_target_or_old_paths():
    r = reader();r.meta.memory[116] = 0
    r.meta.memory[120:132] = struct.pack('<3f', float('inf'), 0, 0)
    assert r.snapshot()['state'] == 'inactive'


def test_cached_reader_resets_on_failure_without_any_write_api():
    from utils.drivers.coordinate_memory import ReadOnlyPosition
    memory = ReadOnlyPosition.__new__(ReadOnlyPosition)
    memory.guidance_reader = SimpleNamespace(snapshot=Mock(side_effect=ValueError('updating')))
    with pytest.raises(ValueError):memory.guidance()
    assert memory.guidance_reader is None
