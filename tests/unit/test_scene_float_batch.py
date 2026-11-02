"""Bounded fresh vector batches; synthetic memory, never game input."""
import struct

import pytest

from tests.unit.test_mono_position import Runtime
from utils.drivers.mono_scene import SceneMetadata


def fixture():
    r = Runtime()
    calls = []
    meta = SceneMetadata(lambda at, n: (calls.append((at, n)), r.read(at, n))[1], r.domain)
    return r, meta, calls


FIELDS = (('_position', 3), ('_rotation', 4), ('__scale', 1))


def test_batch_matches_separate_reads_and_uses_one_payload_read():
    from utils.drivers.mono_scene import read_float_fields
    r, m, calls = fixture()
    r.write(r.hierarchy + 68, struct.pack('<4f', 0, 0, 0, 1))
    r.write(r.hierarchy + 88, struct.pack('<f', 2))
    info = m.obj(r.hierarchy)
    expected = [list(struct.unpack('<' + 'f'*n, m.data(r.hierarchy+m.field(info['address'], name)['offset'], n*4)))
                for name, n in FIELDS]
    calls.clear()
    assert read_float_fields(m, r.hierarchy, info, FIELDS) == expected
    assert calls == [(r.hierarchy+56, 36)]


def test_batch_never_caches_mutable_values():
    from utils.drivers.mono_scene import read_float_fields
    r, m, _ = fixture(); info = m.obj(r.hierarchy)
    first = read_float_fields(m, r.hierarchy, info, FIELDS)
    r.set_position((1, 2, 3))
    second = read_float_fields(m, r.hierarchy, info, FIELDS)
    assert first[0] != second[0] == [1, 2, 3]


@pytest.mark.parametrize('fault', ['outside', 'header', 'static', 'type', 'overlap', 'count'])
def test_invalid_layout_is_rejected_before_payload_read(fault):
    from utils.drivers.mono_scene import read_float_fields
    r, m, calls = fixture(); info = m.obj(r.hierarchy)
    field = m.field(info['address'], '_position')
    if fault == 'outside': field['offset'] = info['size']-4
    if fault == 'header': field['offset'] = 8
    if fault == 'static': field['flags'] |= 16
    if fault == 'type': field['code'] = 18
    if fault == 'overlap': field['offset'] = 68
    fields = (('_position', 100000),) if fault == 'count' else FIELDS
    calls.clear()
    with pytest.raises(ValueError): read_float_fields(m, r.hierarchy, info, fields)
    assert not calls


def test_short_payload_is_not_filled_from_previous_batch():
    from utils.drivers.mono_scene import read_float_fields
    r, m, _ = fixture(); info = m.obj(r.hierarchy)
    read_float_fields(m, r.hierarchy, info, FIELDS)
    m.read = lambda at, n: b'\0'*(n-1)
    with pytest.raises(ValueError, match='变化'): read_float_fields(m, r.hierarchy, info, FIELDS)


@pytest.mark.parametrize('size', [(1, 2, 8), (6, 0, 8), (1, 2, 0)])
def test_navigation_snapshot_uses_fewer_reads_without_changing_collision_geometry(size):
    from tests.unit.test_guidance_planar_scene import plane_reader
    normal = plane_reader(size)
    fast = plane_reader(size); fast.navigation_only = True
    expected = normal.snapshot()
    actual = fast.snapshot()
    assert actual['quality']['navigation_eligible']
    assert actual['colliders'] == expected['colliders']
    assert fast.meta.data.call_count <= normal.meta.data.call_count-5


@pytest.mark.parametrize('size', [(-1, 2, 8), (0, 0, 8), (1, float('nan'), 8)])
def test_batch_keeps_invalid_shape_checks(size):
    from tests.unit.test_guidance_planar_scene import plane_reader
    r = plane_reader(size); r.navigation_only = True
    assert not r.snapshot()['quality']['navigation_eligible']


def test_owner_change_after_batched_geometry_never_publishes_old_wall():
    from tests.unit.test_guidance_planar_scene import plane_reader
    r = plane_reader(); r.navigation_only = True
    ref = r.meta.ref.side_effect; calls = []
    def changed(at, name):
        if (at,name) == (3000,'_group'):
            calls.append(1)
            if len(calls)>1: return 999
        return ref(at,name)
    r.meta.ref.side_effect = changed
    assert not r.snapshot()['colliders']


def test_corrupt_navigation_batch_remains_ineligible():
    from tests.unit.test_guidance_planar_scene import plane_reader
    r = plane_reader(); r.navigation_only = True
    read = r.meta.data.side_effect
    r.meta.data.side_effect = lambda at, n: b'\0'*(n-1) if n == 52 else read(at, n)
    assert not r.snapshot()['quality']['navigation_eligible']
