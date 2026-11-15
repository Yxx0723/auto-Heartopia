# -*- coding: utf-8 -*-
"""Direct large-class reads must be safe without weakening the 512-field cap.

The bug: bus_stations() used static_ref() -> field() -> cls() on TableData, a
generated class with 1000+ fields, and cls() rejects anything above
MONO_FIELD_LIMIT (512). These tests pin both halves of the fix: the ordinary
reader keeps its limit, and the dedicated reader handles 1004/1072/2048 fields
without ever going through cls(), field() or static_ref().
"""
import struct
from unittest.mock import Mock

import pytest

from utils.core import thresholds as policy
from utils.drivers.mono_large_class import (
    TABLE_FIELD_LIMIT, LargeClassReadError, read_large_class_fields)
from utils.drivers.mono_scene import SceneMetadata
from utils.drivers.map_panel import MapPanelReader

DOMAIN = 0x20000
NAME_BYTES = policy.MONO_NAME_BYTES


class FakeMemory:
    """A flat, deterministic Mono address space (no real process is read)."""

    def __init__(self, size=0x1000000):
        self.buf = bytearray(size)
        self.cursor = 0x10000
        self.reads = 0

    def alloc(self, data, align=0x40):
        address = (self.cursor + align - 1) // align * align
        self.buf[address:address + len(data)] = bytes(data)
        self.cursor = address + len(data) + 0x40
        return address

    def read(self, address, size):
        self.reads += 1
        if not 0x10000 <= address < 0x7fffffffffff or size <= 0 \
                or address + size > len(self.buf):
            raise ValueError('场景数据范围无效')
        return bytes(self.buf[address:address + size])

    def text(self, value):
        raw = value.encode('utf-8') + b'\0'
        return self.alloc(raw + b'\0' * (NAME_BYTES - len(raw)))

    def detail(self, code=21, flags=16):
        return self.alloc(struct.pack('<QHB5x', 0, flags, code))

    def metadata(self):
        return SceneMetadata(self.read, DOMAIN)


def build_class(mem, count, specials=(), *, name='TableData', namespace='',
                kind=1, field_table=None, mutate=False):
    """Build a MonoClass header, its field table and supporting name/type data.

    ``specials`` is a list of dicts with keys index/name/code/flags/offset/owner.
    All other fields are harmless fillers owned by the class itself.
    """
    klass = mem.alloc(bytearray(0x110))
    filler_name = mem.text('FillerField')
    filler_detail = mem.detail()
    detail_cache = {}
    table = bytearray(count * 32)
    targets = {s['index']: s for s in specials}
    for index in range(count):
        spec = targets.get(index)
        field_name = mem.text(spec['name']) if spec else filler_name
        code = spec.get('code', 21) if spec else 21
        flags = spec.get('flags', 16) if spec else 16
        if code not in detail_cache:
            detail_cache[code] = mem.detail(code=code, flags=flags)
        typ = detail_cache[code]
        if spec and spec.get('bad_type_pointer'):
            typ = 0x20  # below the valid address floor
        owner = spec.get('owner', klass) if spec else klass
        offset = spec.get('offset', 0x40) if spec else 0
        struct.pack_into('<QQQi', table, index * 32, typ, field_name, owner, offset)
    table_address = mem.alloc(table) if field_table is None else field_table

    header = bytearray(0x110)
    header[0x1b] = kind
    struct.pack_into('<I', header, 0x1c, 0x1000)
    struct.pack_into('<Q', header, 0x48, mem.text(name))
    struct.pack_into('<Q', header, 0x50, mem.text(namespace))
    struct.pack_into('<Q', header, 0xa0, table_address)
    struct.pack_into('<I', header, 0x100, count)
    struct.pack_into('<Q', header, 0x30, 0)  # parent
    mem.buf[klass:klass + 0x110] = header
    if mutate:
        # First header read is clean; the stability witness sees a changed one.
        original, state = mem.read, {'header_reads': 0}

        def changing(address, size, _original=original):
            value = _original(address, size)
            if address == klass and size == 0x110:
                state['header_reads'] += 1
                if state['header_reads'] >= 2:
                    altered = bytearray(value)
                    struct.pack_into('<I', altered, 0x100, count + 1)
                    return bytes(altered)
            return value

        mem.read = changing
    return klass, table_address


def map_elements(index=0, count=1004, *, class_name='TableData', namespace='',
                 kind=1, field_table=None, field_name='TableMapElements',
                 bad_type_pointer=False, owner=None, offset=0x1234, mutate=False):
    mem = FakeMemory()
    spec = {'index': index, 'name': field_name, 'code': 21,
            'flags': 16, 'offset': offset}
    if bad_type_pointer:
        spec['bad_type_pointer'] = True
    if owner is not None:
        spec['owner'] = owner
    klass, table = build_class(mem, count, [spec], name=class_name,
                               namespace=namespace, kind=kind,
                               field_table=field_table, mutate=mutate)
    return mem, klass, table


# --- ordinary 512-field cap is untouched -----------------------------------

def test_ordinary_reader_still_rejects_513_fields():
    assert policy.MONO_FIELD_LIMIT == 512
    mem = FakeMemory()
    klass, _ = build_class(mem, 513)
    with pytest.raises(ValueError, match='场景字段数量无效'):
        mem.metadata().cls(klass)


def test_ordinary_monoclass_reading_still_works():
    mem = FakeMemory()
    klass, _ = build_class(mem, 3, [
        {'index': 0, 'name': 'Alpha', 'code': 8, 'flags': 0, 'offset': 24},
        {'index': 1, 'name': 'Beta', 'code': 21, 'flags': 16, 'offset': 32}])
    info = mem.metadata().cls(klass)
    assert info['name'] == 'TableData'
    assert set(info['fields']) == {'Alpha', 'Beta', 'FillerField'}
    assert info['fields']['Beta']['offset'] == 32


# --- dedicated reader: sizes and boundary ----------------------------------

@pytest.mark.parametrize('count', [1004, 1072])
def test_large_reader_handles_observed_table_data_sizes(count):
    mem, klass, _ = map_elements(count=count)
    read = read_large_class_fields(mem.metadata(), klass, ('TableMapElements',),
                                   expected_name='TableData', expected_namespace='')
    assert read.count == count
    assert read.fields['TableMapElements']['offset'] == 0x1234
    assert read.fields['TableMapElements']['code'] == 21
    assert read.fields['TableMapElements']['flags'] & 16


def test_2048_fields_is_accepted_at_the_boundary():
    mem, klass, _ = map_elements(index=2047, count=2048)
    read = read_large_class_fields(mem.metadata(), klass, ('TableMapElements',))
    assert read.count == TABLE_FIELD_LIMIT == 2048


def test_more_than_2048_fields_is_rejected():
    mem, klass, _ = map_elements(count=2049)
    with pytest.raises(LargeClassReadError, match='大字段数量无效'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',))


# --- invalid pointers and invalid field tables -----------------------------

def test_null_field_table_pointer_is_rejected():
    mem, klass, _ = map_elements(count=1004, field_table=0)
    with pytest.raises(LargeClassReadError, match='字段表指针为空'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',))


def test_invalid_type_pointer_is_rejected_not_swallowed():
    mem, klass, _ = map_elements(count=1004, bad_type_pointer=True)
    with pytest.raises(ValueError, match='场景数据范围无效'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',))


def test_invalid_class_kind_is_rejected():
    mem, klass, _ = map_elements(count=1004, kind=9)
    with pytest.raises(LargeClassReadError, match='大字段类型头无效'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',))


def test_field_owned_by_another_class_is_rejected():
    mem, klass, _ = map_elements(count=1004, owner=0x30000)
    with pytest.raises(LargeClassReadError, match='大字段归属不符'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',))


def test_class_identity_must_match_table_data():
    mem, klass, _ = map_elements(count=1004, class_name='SomeOtherTable')
    with pytest.raises(LargeClassReadError, match='大字段类型身份不符'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',),
                                expected_name='TableData')


def test_namespace_identity_must_match():
    mem, klass, _ = map_elements(count=1004, namespace='Unexpected')
    with pytest.raises(LargeClassReadError, match='大字段命名空间不符'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',),
                                expected_name='TableData', expected_namespace='')


def test_mutating_metadata_is_rejected():
    mem, klass, _ = map_elements(count=1004, mutate=True)
    with pytest.raises(LargeClassReadError, match='大字段元数据正在变化'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',))


# --- exact match ------------------------------------------------------------

@pytest.mark.parametrize('lookalike', ['TableMapElement', 'TableMapElementsX',
                                       'tableMapElements'])
def test_table_map_elements_requires_an_exact_name(lookalike):
    mem, klass, _ = map_elements(count=1004, field_name=lookalike)
    with pytest.raises(LargeClassReadError, match='大字段未唯一确认'):
        read_large_class_fields(mem.metadata(), klass, ('TableMapElements',),
                                expected_name='TableData')


# --- static reference path and bus-station dictionary -----------------------

def bus_panel(count=1004):
    mem = FakeMemory()
    klass, _ = build_class(mem, count, [
        {'index': 0, 'name': 'TableMapElements', 'offset': 0x1234}])
    meta = mem.metadata()
    rows = [0x510000, 0x520000, 0x530000]
    values = {0x510000: (101, 1), 0x520000: (102, 0), 0x530000: (103, 7)}
    stamp = ('stable',)
    meta.dictionary = Mock(return_value=(rows, stamp))
    meta.static_reference_at = Mock(return_value=0x600000)
    panel = MapPanelReader.__new__(MapPanelReader)
    panel.meta = meta
    panel.classes = {}
    panel.last_table_field_count = None
    panel.find_class = Mock(return_value=klass)
    panel.expected = Mock(return_value=None)
    panel.payload = Mock(side_effect=lambda obj, name, code, width:
                         struct.pack('<i', values[obj][0 if name == 'id' else 1]))
    panel._map_markers = Mock(side_effect=lambda category, ids=None:
                              {key: (0., 0., 0.) for key in (ids or set())})
    return panel, meta, klass


def test_static_reference_read_never_enters_the_512_field_path():
    panel, meta, klass = bus_panel()
    meta.cls = Mock(side_effect=AssertionError('cls() must not be used'))
    meta.field = Mock(side_effect=AssertionError('field() must not be used'))
    meta.static_ref = Mock(side_effect=AssertionError('static_ref() must not be used'))
    panel.bus_stations()
    meta.static_reference_at.assert_called()
    assert meta.static_reference_at.call_args.args == (klass, 0x1234)
    meta.cls.assert_not_called()
    meta.field.assert_not_called()
    meta.static_ref.assert_not_called()
    assert panel.last_table_field_count == 1004


def test_bus_station_dictionary_is_parsed_by_bus_station_flag():
    panel, meta, _ = bus_panel(count=1072)
    result = panel.bus_stations()
    # 101 and 103 are bus stations; 102 is not.
    panel._map_markers.assert_called_once()
    assert panel._map_markers.call_args.args[0] == 1
    assert set(panel._map_markers.call_args.args[1]) == {101, 103}
    assert set(result) == {101, 103}
    assert panel.last_table_field_count == 1072
    meta.dictionary.assert_called_with(0x600000, max_items=4096, with_stamp=True)


def test_duplicate_or_missing_fields_are_errors_not_empty_results():
    panel, _, _ = bus_panel()
    panel._map_element_table_field = Mock(side_effect=LargeClassReadError('bad'))
    with pytest.raises(LargeClassReadError, match='bad'):
        panel.bus_stations()
    assert panel._map_markers.call_count == 0
