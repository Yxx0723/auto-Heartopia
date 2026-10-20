"""Read-only metadata for the version-pinned native scene runtime.

Layouts were checked against this installed GameAssembly's exported accessors.
No remote function calls, memory writes, injection or debugger are used.
"""
import struct


class NativeSceneMetadata:
    # GameAssembly fingerprint is checked by ReadOnlyPosition before use.
    _class_cache_rva = 0x2f850c8
    _type_stride = 88
    _type_count = 14794

    def __init__(self, read, assembly_base):
        self.read = read
        self.root_at = assembly_base + self._class_cache_rva
        self.root = self.pointer(self.root_at)
        self.cache = {}
        xd = self.class_at(7737, 'XDCollider', 'XDT.Physics')
        self.type_start = self.pointer(xd['address'] + 0x20) - 7737 * self._type_stride

    def data(self, at, size):
        if not 0x10000 <= at < 0x7fffffffffff or not 0 < size <= 8 * 1024 * 1024:
            raise ValueError('原生场景读取范围无效')
        raw = self.read(at, size)
        if raw is None or len(raw) != size:
            raise ValueError('原生场景数据正在变化')
        return raw

    def pointer(self, at):
        value = struct.unpack('<Q', self.data(at, 8))[0]
        if value and not 0x10000 <= value < 0x7fffffffffff:
            raise ValueError('原生场景引用无效')
        return value

    def integer(self, at):
        return struct.unpack('<i', self.data(at, 4))[0]

    def text(self, at):
        raw = self.data(at, 256)
        if b'\0' not in raw:
            raise ValueError('原生场景类型名称过长')
        value = raw.split(b'\0', 1)[0].decode('utf-8')
        if any(ord(c) < 32 for c in value):
            raise ValueError('原生场景类型名称无效')
        return value

    def cls(self, at):
        if at in self.cache:
            return self.cache[at]
        raw = self.data(at, 0x138)
        size, count = struct.unpack_from('<I', raw, 0xf8)[0], struct.unpack_from('<H', raw, 0x120)[0]
        if not 16 <= size <= 1024 * 1024 or count > 2048:
            raise ValueError('原生场景类型布局无效')
        table = struct.unpack_from('<Q', raw, 0x80)[0]
        fields = {}
        for i in range(count):
            name, typ, owner, offset = struct.unpack('<QQQi', self.data(table + i * 32, 28))
            if owner != at:
                raise ValueError('原生场景字段归属不符')
            details = self.data(typ, 16)
            fields[self.text(name)] = {'offset': offset, 'code': details[10],
                                      'flags': struct.unpack_from('<H', details, 8)[0],
                                      'type': struct.unpack_from('<Q', details)[0]}
        result = {'address': at, 'name': self.text(struct.unpack_from('<Q', raw, 0x10)[0]),
                  'namespace': self.text(struct.unpack_from('<Q', raw, 0x18)[0]),
                  'parent': struct.unpack_from('<Q', raw, 0x58)[0],
                  'element': struct.unpack_from('<Q', raw, 0x40)[0], 'size': size,
                  'value_type': bool(raw[0x132] & 2), 'fields': fields}
        self.cache[at] = result
        return result

    def class_at(self, index, name=None, namespace=None):
        if not 0 <= index < self._type_count:
            raise ValueError('原生场景类型索引无效')
        info = self.cls(self.pointer(self.root + index * 8))
        if name is not None and (info['name'], info['namespace']) != (name, namespace):
            raise ValueError('原生场景类型与当前版本不符')
        return info

    def field_type(self, field):
        if field['code'] not in (17, 18):
            raise ValueError('原生场景字段不是具名类型')
        offset = field['type'] - self.type_start
        if offset < 0 or offset % self._type_stride:
            raise ValueError('原生场景字段类型归属不符')
        info = self.class_at(offset // self._type_stride)
        if self.pointer(info['address'] + 0x20) != field['type']:
            raise ValueError('原生场景字段类型已变化')
        return info

    def obj(self, at, expected=None):
        info = self.cls(self.pointer(at))
        if expected and (info['name'], info['namespace']) != expected:
            raise ValueError('原生场景对象类型不符')
        return info

    def field(self, klass, name, code=None, static=False):
        seen = set()
        while klass and klass not in seen and len(seen) < 32:
            seen.add(klass)
            info = self.cls(klass)
            if name in info['fields']:
                f = info['fields'][name]
                if (code is not None and f['code'] != code) or bool(f['flags'] & 16) != static \
                        or f['offset'] < (0 if static else 16) or f['flags'] & 64:
                    raise ValueError('原生场景字段布局不符：' + name)
                return f
            klass = info['parent']
        raise ValueError('原生场景对象缺少字段：' + name)

    def ref(self, obj, name):
        info = self.obj(obj)
        f = self.field(info['address'], name)
        if f['code'] not in (18, 21, 28, 29, 14) or f['offset'] > info['size'] - 8:
            raise ValueError('原生场景引用布局不符')
        return self.pointer(obj + f['offset'])

    def static_ref(self, klass, name):
        f = self.field(klass, name, static=True)
        if f['code'] not in (18, 21, 28, 29):
            raise ValueError('原生场景静态引用布局不符')
        return self.pointer(self.pointer(klass + 0xb8) + f['offset'])

    def array_refs(self, at, expected=None):
        element, count = self.array(at)
        if element['value_type'] or (expected and (element['name'], element['namespace']) != expected):
            raise ValueError('原生场景数组元素类型不符')
        raw = self.data(at + 32, count * 8) if count else b''
        if self.array(at)[1] != count:
            raise ValueError('原生场景数组发生变化')
        return list(struct.unpack('<' + 'Q' * count, raw))

    def container_stamp(self, at, dictionary=False):
        info = self.obj(at)
        fields = ('entries', 'count', 'version') if dictionary else ('_items', '_size', '_version')
        return (self.ref(at, fields[0]),
                self.integer(at + self.field(info['address'], fields[1], 8)['offset']),
                self.integer(at + self.field(info['address'], fields[2], 8)['offset']))

    def string(self, at):
        if not at:
            return ''
        self.obj(at, ('String', 'System'))
        length = self.integer(at + 16)
        if not 0 <= length <= 1024:
            raise ValueError('原生场景字符串范围无效')
        value = self.data(at + 20, length * 2).decode('utf-16le') if length else ''
        if self.integer(at + 16) != length:
            raise ValueError('原生场景字符串发生变化')
        return value

    def array(self, at):
        info = self.obj(at)
        length = struct.unpack('<Q', self.data(at + 24, 8))[0]
        if not info['name'].endswith(']') or length > 200000:
            raise ValueError('原生场景数组范围无效')
        return self.cls(info['element']), length

    def list_data(self, at):
        info = self.obj(at, ('List`1', 'System.Collections.Generic'))
        size_at = at + self.field(info['address'], '_size', 8)['offset']
        version_at = at + self.field(info['address'], '_version', 8)['offset']
        version, size = self.integer(version_at), self.integer(size_at)
        array = self.ref(at, '_items')
        element, capacity = self.array(array)
        if not 0 <= size <= capacity:
            raise ValueError('原生场景列表范围无效')
        stride = element['size'] - 16 if element['value_type'] else 8
        if not 0 < stride <= 4096:
            raise ValueError('原生场景列表元素布局无效')
        raw = self.data(array + 32, size * stride) if size else b''
        if self.integer(version_at) != version or self.integer(size_at) != size or self.ref(at, '_items') != array:
            raise ValueError('原生场景列表发生变化')
        return element, raw, stride

    def list_refs(self, at):
        element, raw, stride = self.list_data(at)
        if element['value_type'] or stride != 8:
            raise ValueError('原生场景列表不是引用集合')
        return list(struct.unpack('<' + 'Q' * (len(raw) // 8), raw))

    def dictionary(self, at):
        info = self.obj(at, ('Dictionary`2', 'System.Collections.Generic'))
        version_at = at + self.field(info['address'], 'version', 8)['offset']
        count_at = at + self.field(info['address'], 'count', 8)['offset']
        version, count = self.integer(version_at), self.integer(count_at)
        if not 0 <= count <= 200000:
            raise ValueError('原生场景字典范围无效')
        if not count:
            return []
        entries = self.ref(at, 'entries')
        element, capacity = self.array(entries)
        stride = element['size'] - 16
        if count > capacity or not element['value_type'] or not 16 <= stride <= 256:
            raise ValueError('原生场景字典布局无效')
        hashed = self.field(element['address'], 'hashCode', 8)['offset'] - 16
        value_field = self.field(element['address'], 'value')
        if value_field['code'] not in (18, 21, 28, 29):
            raise ValueError('原生场景字典不是引用集合')
        value = value_field['offset'] - 16
        if not 0 <= hashed <= stride - 4 or not 0 <= value <= stride - 8:
            raise ValueError('原生场景字典字段范围无效')
        raw = self.data(entries + 32, count * stride)
        result = [struct.unpack_from('<Q', raw, i * stride + value)[0] for i in range(count)
                  if struct.unpack_from('<i', raw, i * stride + hashed)[0] >= 0]
        if self.integer(version_at) != version or self.integer(count_at) != count or self.ref(at, 'entries') != entries:
            raise ValueError('原生场景字典发生变化')
        return result

    def validate_root(self):
        if self.pointer(self.root_at) != self.root:
            raise ValueError('原生场景运行时已切换')
