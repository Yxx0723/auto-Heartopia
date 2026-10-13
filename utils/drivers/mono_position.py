"""Version-pinned Mono metadata reader. Uses only an injected read operation.

Layout measured from local mono-2.0-sgen.dll exports on 2026-10-12. No remote
function calls, debugger, memory writes, or saved gameplay heap addresses.
"""
import struct

from utils.core import thresholds as limits
from utils.core.coordinate_route import valid_position


ROLE_TYPES = {
    'Character': 'XDTLevelAndEntity.Game.GameMode',
    'LocalPlayerComponent': 'XDTLevelAndEntity.Gameplay.Component.Player',
    'Entity': 'XDTGame.GAS.Client',
    'LevelEntityComponent': 'XDTLevelAndEntity.EntityView',
    'Hierarchy': 'XDTGame.Core',
}


class MonoPositionLocator:
    def __init__(self, read, base):
        self.read, self.base = read, base
        self.classes = {}
        self.domain = self._pointer(base + 0x4d3878)
        self.character_class = self._discover_character()

    def _bytes(self, address, size):
        if not 0x10000 <= address < 0x7fffffffffff or size <= 0:
            raise ValueError('角色对象地址无效，请等待角色进入场景')
        data = self.read(address, size)
        if data is None or len(data) != size:
            raise ValueError('角色对象无法读取，请重新连接')
        return data

    def _pointer(self, address, allow_null=False):
        value = struct.unpack('<Q', self._bytes(address, 8))[0]
        if value == 0 and allow_null:
            return 0
        if not 0x10000 <= value < 0x7fffffffffff:
            raise ValueError('角色对象引用已失效，请等待角色进入场景')
        return value

    def _name(self, address):
        data = self._bytes(address, limits.MONO_NAME_BYTES)
        if b'\0' not in data:
            raise ValueError('游戏类型名称不完整')
        return data.split(b'\0', 1)[0].decode('utf-8')

    def _discover_character(self):
        code = self._bytes(self.base + 0x29dd35, 7)
        if code[:3] != b'\x48\x8b\x1d':
            raise ValueError('游戏运行时与坐标配置不匹配')
        root = self.base + 0x29dd3c + struct.unpack('<i', code[3:])[0]
        node = self._pointer(root)
        seen, images = set(), []
        while node:
            if node in seen or len(seen) >= limits.MONO_ASSEMBLY_LIMIT:
                raise ValueError('游戏程序集列表无效')
            seen.add(node)
            image = self._pointer(self._pointer(node) + 0x68)
            if self._name(self._pointer(image + 0x30)) == 'XDTLevelAndEntity':
                images.append(image)
            node = self._pointer(node + 8, allow_null=True)
        if len(images) != 1:
            raise ValueError('角色程序集未加载或存在歧义，请进入角色后重新连接')
        image = images[0]
        header = self._bytes(image + 0x4d0, 40)
        size, count, buckets = struct.unpack_from('<IIQ', header, 24)
        if not 0 < size <= limits.MONO_CLASS_LIMIT or not 0 < count <= limits.MONO_CLASS_LIMIT:
            raise ValueError('游戏类型缓存范围无效')
        data = self._bytes(buckets, size * 8)
        found, visited = [], set()
        for klass in struct.unpack('<' + 'Q' * size, data):
            while klass:
                if klass in visited or len(visited) >= count:
                    raise ValueError('游戏类型缓存存在循环或数量异常')
                visited.add(klass)
                raw = self._bytes(klass, 0x110)
                if raw[0x1b] not in (1, 2) or struct.unpack_from('<Q', raw, 0x40)[0] != image:
                    raise ValueError('游戏类型缓存归属无效')
                if self._name(struct.unpack_from('<Q', raw, 0x48)[0]) == 'Character' \
                        and self._name(struct.unpack_from('<Q', raw, 0x50)[0]) == ROLE_TYPES['Character']:
                    found.append(klass)
                klass = struct.unpack_from('<Q', raw, 0x108)[0]
        if len(found) != 1:
            raise ValueError('本地角色类型尚未加载或存在歧义')
        return found[0]

    def _class(self, klass):
        if klass in self.classes:
            return self.classes[klass]
        raw = self._bytes(klass, 0x110)
        if raw[0x1b] not in (1, 2):
            raise ValueError('角色类型布局不受支持')
        count = struct.unpack_from('<I', raw, 0x100)[0]
        size = struct.unpack_from('<I', raw, 0x1c)[0]
        if count > limits.MONO_FIELD_LIMIT or not 16 <= size <= limits.MONO_OBJECT_LIMIT:
            raise ValueError('角色字段范围无效')
        fields = []
        if count:
            data = self._bytes(struct.unpack_from('<Q', raw, 0xa0)[0], count * 32)
            for index in range(count):
                typ, name, owner, offset = struct.unpack_from('<QQQi', data, index * 32)
                detail = self._bytes(typ, 16)
                fields.append({'name': self._name(name), 'offset': offset,
                    'flags': struct.unpack_from('<H', detail, 8)[0], 'code': detail[10],
                    'type': struct.unpack_from('<Q', detail)[0]})
        result = {'name': self._name(struct.unpack_from('<Q', raw, 0x48)[0]),
            'namespace': self._name(struct.unpack_from('<Q', raw, 0x50)[0]),
            'parent': struct.unpack_from('<Q', raw, 0x30)[0], 'fields': fields, 'size': size}
        self.classes[klass] = result
        return result

    def _field(self, klass, name, code, static=False):
        seen = set()
        while klass:
            if klass in seen or len(seen) >= limits.MONO_PARENT_LIMIT:
                raise ValueError('角色类型继承关系无效')
            seen.add(klass)
            definition = self._class(klass)
            found = [field for field in definition['fields'] if field['name'] == name]
            if found:
                field = found[0]
                width = 12 if code == 17 else 1 if code == 2 else 8
                if len(found) != 1 or field['code'] != code or bool(field['flags'] & 16) != static \
                        or field['flags'] & 64 or field['offset'] < (0 if static else 16) \
                        or field['offset'] + width > definition['size']:
                    raise ValueError('角色字段布局不匹配：' + name)
                return field
            klass = definition['parent']
        raise ValueError('缺少角色字段：' + name)

    def _vtable(self, vtable, klass=None):
        actual = self._pointer(vtable)
        if (klass is not None and actual != klass) or self._pointer(vtable + 16) != self.domain:
            raise ValueError('角色对象运行时归属已变化')
        return actual

    def _object(self, address, expected):
        klass = self._vtable(self._pointer(address))
        definition = self._class(klass)
        if definition['name'] != expected or definition['namespace'] != ROLE_TYPES[expected]:
            raise ValueError('读取对象不是本地角色：' + expected)
        return klass

    def snapshot(self):
        checks, trace = [], []

        def follow(obj, expected, name, target):
            klass = self._object(obj, expected)
            field = self._field(klass, name, 18)
            at = obj + field['offset']
            value = self._pointer(at)
            self._object(value, target)
            checks.append((at, value))
            trace.append({'read_at': at, 'pointer': value, 'offset': field['offset'], 'field': name})
            return value

        klass = self.character_class
        vtable = self._pointer(klass + 0xd8)
        self._vtable(vtable, klass)
        if not self._bytes(vtable + 0x38, 1)[0] & 2:
            raise ValueError('角色静态引用尚未初始化')
        slots = struct.unpack('<I', self._bytes(klass + 0x5c, 4))[0]
        if slots >= limits.MONO_VTABLE_LIMIT:
            raise ValueError('角色虚函数表范围无效')
        storage = self._pointer(vtable + 0x50 + 8 * slots)
        root_at = storage + self._field(klass, '_character', 18, static=True)['offset']
        character = self._pointer(root_at)
        checks.append((root_at, character))
        player = follow(character, 'Character', 'player', 'LocalPlayerComponent')
        if follow(player, 'LocalPlayerComponent', '_character', 'Character') != character:
            raise ValueError('本地角色双向引用不一致')
        entity = follow(player, 'LocalPlayerComponent', '<entity>k__BackingField', 'Entity')
        entity_class = self._object(entity, 'Entity')
        flags = [entity + self._field(entity_class, name, 2)['offset']
                 for name in ('<isSelfPlayer>k__BackingField', '_alived')]
        if any(self._bytes(at, 1) != b'\x01' for at in flags):
            raise ValueError('角色尚未进入可读取场景')
        transform = follow(entity, 'Entity', '<transformComponent>k__BackingField', 'LevelEntityComponent')
        if follow(transform, 'LevelEntityComponent', '<entity>k__BackingField', 'Entity') != entity:
            raise ValueError('角色变换归属不一致')
        hierarchy = follow(transform, 'LevelEntityComponent', '_hierarchy', 'Hierarchy')
        if follow(hierarchy, 'Hierarchy', '_hierarchyObject', 'LevelEntityComponent') != transform:
            raise ValueError('角色层级归属不一致')
        hierarchy_class = self._object(hierarchy, 'Hierarchy')
        parent_at = hierarchy + self._field(hierarchy_class, '_parent', 18)['offset']
        if self._pointer(parent_at, allow_null=True):
            raise ValueError('角色正附着于其他对象，暂停世界坐标读取')
        field = self._field(hierarchy_class, '_position', 17)
        vector_type = self._class(field['type'])
        if vector_type['name'] != 'Vector3' or vector_type['namespace'] != 'UnityEngine' \
                or vector_type['size'] != 28:
            raise ValueError('角色位置字段类型不匹配')
        address = hierarchy + field['offset']
        position = struct.unpack('<3f', self._bytes(address, 12))
        if not valid_position(position):
            raise ValueError('角色坐标数值无效')
        # A scene transition can replace any link between reads. Recheck identity
        # after XYZ so one sample cannot combine two different role graphs.
        if any(self._pointer(at, allow_null=True) != value for at, value in checks) \
                or self._pointer(parent_at, allow_null=True) \
                or any(self._bytes(at, 1) != b'\x01' for at in flags):
            raise ValueError('角色或场景在采样中发生变化，请重新连接')
        return {'position': position, 'address': address, 'module_base': self.base,
            'locator': 'mono-local-player', 'valid_paths': 1, 'configured_paths': 1,
            'paths': [{'label': 'Character → LocalPlayerComponent → Entity → Hierarchy._position',
                       'address': address, 'position': position, 'trace': trace}]}
