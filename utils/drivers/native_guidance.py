"""Read current owned task-guidance lists; never invoke the game's planner."""
import hashlib
import json
import math
import struct
import time

from utils.core import thresholds as policy
from utils.drivers.mono_scene import SceneMetadata


class NativeGuidanceReader:
    def __init__(self, memory, clock=time.monotonic):
        if memory.locator is None:
            raise ValueError('当前版本不支持只读游戏引导')
        self.memory, self.clock = memory, clock
        self.meta = SceneMetadata(memory._read, memory.locator.domain)
        self.world_class = None
        self.cached_module = None

    def _world(self):
        m, base = self.meta, self.memory.base
        code = m.data(base+0x29dd35, 7)
        if code[:3] != b'\x48\x8b\x1d':
            raise ValueError('游戏程序集入口版本不符')
        root = base+0x29dd3c+struct.unpack('<i', code[3:])[0]
        first = node = m.pointer(root)
        seen, images = set(), []
        while node:
            if node in seen or len(seen) >= policy.MONO_ASSEMBLY_LIMIT:
                raise ValueError('游戏程序集列表正在变化')
            seen.add(node)
            image = m.pointer(m.pointer(node)+0x68)
            if m.text(m.pointer(image+0x30)) == 'XDTBaseService':
                images.append(image)
            node = m.pointer(node+8)
        if len(images) != 1 or m.pointer(root) != first:
            raise ValueError('游戏世界程序集未确认')
        image, = images
        header = m.data(image+0x4d0, 40)
        size, count, buckets = struct.unpack_from('<IIQ', header, 24)
        if not 0 < size <= policy.MONO_CLASS_LIMIT or not 0 < count <= policy.MONO_CLASS_LIMIT:
            raise ValueError('游戏世界类型范围无效')
        raw_buckets = m.data(buckets, size*8)
        seen, found = set(), []
        for cls in struct.unpack('<'+'Q'*size, raw_buckets):
            while cls:
                if cls in seen or len(seen) >= count:
                    raise ValueError('游戏世界类型缓存正在变化')
                seen.add(cls)
                raw = m.data(cls, 0x110)
                if raw[0x1b] not in (1, 2) or struct.unpack_from('<Q', raw, 0x40)[0] != image:
                    raise ValueError('游戏世界类型归属不符')
                if m.text(struct.unpack_from('<Q', raw, 0x48)[0]) == 'GameWorld' \
                        and m.text(struct.unpack_from('<Q', raw, 0x50)[0]) == 'XDTGame.Core':
                    found.append(cls)
                cls = struct.unpack_from('<Q', raw, 0x108)[0]
        if len(found) != 1 or m.data(image+0x4d0, 40) != header or m.data(buckets, size*8) != raw_buckets:
            raise ValueError('游戏世界类型尚未稳定')
        return found[0]

    def _owner(self):
        m = self.meta
        if self.world_class is None:
            self.world_class = self._world()
        level = m.static_ref(self.world_class, '_currentLevel')
        array = m.ref(m.ref(m.ref(level, '_viewModules'), 'group'), '_modules')
        _, count = m.array(array)
        cached = self.cached_module
        if cached and cached[:2] == (level, array) and cached[2] < count \
                and m.pointer(array+32+cached[2]*8) == cached[3]:
            module = cached[3]
        else:
            if count > 2048:
                raise ValueError('游戏引导模块范围超限')
            found = []
            for index, obj in enumerate(m.array_refs(array)):
                if not obj:
                    continue
                info = m.obj(obj)
                if (info['name'], info['namespace']) == ('TrackingPathModule', 'XDTLevelAndEntity.GameplaySystem.TrackingPoint'):
                    found.append((index, obj))
            if len(found) != 1:
                raise ValueError('当前场景没有唯一的游戏引导模块')
            index, module = found[0]
            self.cached_module = (level, array, index, module)
        info = m.obj(module)
        if (info['name'], info['namespace']) != ('TrackingPathModule', 'XDTLevelAndEntity.GameplaySystem.TrackingPoint'):
            raise ValueError('游戏引导模块归属已变化')
        return level, array, module

    def _vectors(self, obj):
        m = self.meta
        info = m.obj(obj)
        if (info['name'], info['namespace']) != ('List`1', 'System.Collections.Generic'):
            raise ValueError('游戏引导路径集合类型不符')
        size = m.field(info['address'], '_size')
        version = m.field(info['address'], '_version')
        if any(f['code'] != 8 or f['flags'] & 16 or not 16 <= f['offset'] <= info['size']-4
               for f in (size, version)):
            raise ValueError('游戏引导路径集合布局无效')
        def stamp():
            return (m.integer(obj+size['offset']), m.integer(obj+version['offset']), m.ref(obj, '_items'))
        before = stamp()
        count, _, items = before
        if not 0 <= count <= policy.COORD_GUIDANCE_MAX_POINTS:
            raise ValueError('游戏引导路径点数超出范围')
        array, capacity = m.array(items)
        element = m.cls(array['element'])
        if (element['name'], element['namespace'], element['size']) != ('Vector3', 'UnityEngine', 28) or count > capacity:
            raise ValueError('游戏引导路径元素类型不符')
        raw = m.data(items+32, count*12) if count else b''
        if stamp() != before:
            raise ValueError('游戏引导路径正在更新')
        points = tuple(struct.iter_unpack('<3f', raw))
        if any(not math.isfinite(v) or abs(v) > 100000 for p in points for v in p):
            raise ValueError('游戏引导路径包含无效坐标')
        return before, points

    def snapshot(self):
        m = self.meta
        owner = self._owner()
        module = owner[2]
        info = m.obj(module)
        enabled = m.field(info['address'], 'isNavigating')
        target = m.field(info['address'], 'end')
        if enabled['code'] != 2 or target['code'] != 17 or enabled['flags'] & 16 or target['flags'] & 16 \
                or not 16 <= enabled['offset'] < info['size'] or not 16 <= target['offset'] <= info['size']-12:
            raise ValueError('游戏引导状态字段类型不符')
        def state():
            return (m.data(module+enabled['offset'], 1), m.data(module+target['offset'], 12))
        before = state()
        if before[0] == b'\0':
            return {'state': 'inactive', 'captured_at': self.clock()}
        goal = struct.unpack('<3f', before[1])
        if not all(math.isfinite(v) and abs(v) <= 100000 for v in goal):
            raise ValueError('游戏尚未确认引导目标')
        paths = {}
        for name in ('realPathList', '_path'):
            obj = m.ref(module, name)
            stamp, points = self._vectors(obj)
            paths[name] = (obj, stamp, points)
        chosen = paths['realPathList'][2] or paths['_path'][2]
        # Verify exactly the relevant collections/target, not changing frame
        # timers elsewhere in the module. No atomic-process-snapshot claim.
        for name, (obj, stamp, points) in paths.items():
            if m.ref(module, name) != obj or self._vectors(obj) != (stamp, points):
                raise ValueError('游戏引导路径正在更新')
        if state() != before or self._owner() != owner:
            raise ValueError('游戏追踪目标或场景已变化')
        if len(chosen) < 2:
            raise ValueError('游戏地面引导路径尚未生成')
        context = hashlib.sha256(json.dumps([self.memory.profile['game_assembly_sha256'],
            self.memory.pid, self.memory.locator.domain, owner[0], module]).encode()).hexdigest()
        return {'state': 'ready', 'context': context, 'target': goal, 'path': chosen,
                'target_gap_xz': math.hypot(chosen[-1][0]-goal[0], chosen[-1][2]-goal[2]),
                'captured_at': self.clock()}
