# -*- coding: utf-8 -*-
"""Direct MonoClass field-table reads for generated classes above 512 fields.

``SceneMetadata.cls()`` rejects a class whose field count exceeds
``policy.MONO_FIELD_LIMIT`` (512). Generated configuration classes such as
``TableData`` legitimately declare 1000+ fields, so a reader that needs one of
their static fields cannot go through ``cls()``, ``field()`` or ``static_ref()``:
all three eventually hit that cap.

This module reads the MonoClass header and its field table directly under a
separate ``TABLE_FIELD_LIMIT = 2048`` bound. It deliberately does **not** relax
the ordinary reader: the 512 limit, its object/size checks and every existing
safety check stay untouched. All offsets used here are ones already validated by
``SceneMetadata.cls()``, ``SceneMetadata.static_reference_at()`` and
``ItemNameTable._fields()``; no new Mono memory offset is introduced.

Only the raw field metadata is returned. Whether a field is static, its type
code and its offset bound remain the caller's decision, so callers keep their
own rejection rules instead of inheriting a new global policy.
"""
import struct

# TableData was observed with 1004 declared fields at runtime (1072 in the
# decompiled assembly). 2048 leaves headroom without becoming a general cap.
TABLE_FIELD_LIMIT = 2048

# Offsets already used by SceneMetadata.cls() / ItemNameTable._fields().
_CLASS_HEADER_SIZE = 0x110
_CLASS_KIND = 0x1b
_CLASS_NAME = 0x48
_CLASS_NAMESPACE = 0x50
_CLASS_FIELD_TABLE = 0xa0
_CLASS_FIELD_COUNT = 0x100
_FIELD_ENTRY_SIZE = 32
_FIELD_ENTRY_BYTES = 28
_TYPE_DETAIL_SIZE = 16


class LargeClassReadError(ValueError):
    """The generated class could not be read without weakening a check."""


class LargeClassFields:
    """One direct field-table read plus the witness needed to trust it later."""

    def __init__(self, klass, count, header, table, fields):
        self.klass = klass
        self.count = count
        self.header = header
        self.table = table
        self.fields = fields  # name -> {'offset','code','flags','type','entry','raw'}

    def unchanged(self, meta):
        """The header and field-table pointer must not move after the read."""
        if self.header is not None \
                and meta.data(self.klass, _CLASS_HEADER_SIZE) != self.header:
            return False
        return meta.pointer(self.klass + _CLASS_FIELD_TABLE) == self.table


def read_large_class_fields(meta, klass, wanted, *, limit=TABLE_FIELD_LIMIT,
                            expected_name=None, expected_namespace=None,
                            count=None, table=None, check=None):
    """Locate ``wanted`` fields directly in a large generated class.

    ``meta`` must expose ``data``/``pointer``/``text`` only. ``cls()``,
    ``field()`` and ``static_ref()`` are never called, so the 512-field cap
    cannot be reached from here. Every failure raises ``LargeClassReadError``
    or the underlying read error; a failed read is never returned as an empty
    mapping.
    """
    header = None
    if count is None or table is None:
        # Full header validation for callers that only have the class address.
        header = meta.data(klass, _CLASS_HEADER_SIZE)
        if header[_CLASS_KIND] not in (1, 2):
            raise LargeClassReadError('大字段类型头无效')
        count = struct.unpack_from('<I', header, _CLASS_FIELD_COUNT)[0]
        if expected_name is not None:
            name = meta.text(meta.pointer(klass + _CLASS_NAME))
            if name != expected_name:
                raise LargeClassReadError('大字段类型身份不符')
            if expected_namespace is not None:
                namespace = meta.text(meta.pointer(klass + _CLASS_NAMESPACE))
                if namespace != expected_namespace:
                    raise LargeClassReadError('大字段命名空间不符')
        table = meta.pointer(klass + _CLASS_FIELD_TABLE)
    # A caller that already read the class header still gets the same bounds.
    if not 0 < count <= limit:
        raise LargeClassReadError(f'大字段数量无效（{count}，上限 {limit}）')
    if not table:
        raise LargeClassReadError('大字段字段表指针为空')

    wanted = set(wanted)
    found = {}
    for index in range(count):
        if check is not None:
            check()
        address = index * _FIELD_ENTRY_SIZE
        entry = table + address
        # One 28-byte entry at a time, exactly as SceneMetadata.cls() does.
        raw = meta.data(entry, _FIELD_ENTRY_BYTES)
        typ, name_ptr, owner, offset = struct.unpack('<QQQi', raw)
        if owner != klass:
            raise LargeClassReadError('大字段归属不符')
        name = meta.text(name_ptr)
        if name not in wanted:
            continue
        if name in found:
            raise LargeClassReadError('大字段名称重复')
        details = meta.data(typ, _TYPE_DETAIL_SIZE)
        found[name] = {
            'offset': offset,
            'code': details[10],
            'flags': struct.unpack_from('<H', details, 8)[0],
            'type': struct.unpack_from('<Q', details)[0],
            'entry': entry,
            'raw': raw,
        }
    missing = wanted - set(found)
    if missing:
        raise LargeClassReadError('大字段未唯一确认：' + ','.join(sorted(missing)))
    result = LargeClassFields(klass, count, header, table, found)
    if not result.unchanged(meta):
        raise LargeClassReadError('大字段元数据正在变化')
    return result
