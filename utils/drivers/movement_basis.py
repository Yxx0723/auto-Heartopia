"""Read the local player's current camera-forward vector without moving them."""
import math
import struct

from utils.drivers.mono_scene import SceneMetadata


def camera_basis(forward):
    if not isinstance(forward, (tuple, list)) or len(forward) != 3 \
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in forward):
        raise ValueError('镜头方向数据无效')
    length = math.sqrt(sum(v*v for v in forward))
    planar = math.hypot(forward[0], forward[2])
    if not .9 <= length <= 1.1 or planar < .05:
        raise ValueError('镜头朝向暂时无法用于水平移动')
    x, z = forward[0]/planar, forward[2]/planar
    return {'forward': [x, z], 'right': [z, -x]}


class MovementBasisReader:
    def __init__(self, read, locator):
        self.meta, self.locator = SceneMetadata(read, locator.domain), locator

    def snapshot(self):
        sample = self.locator.snapshot()
        if sample.get('attached_to_parent'):
            raise ValueError('请先下车再进行步行回放')
        m = self.meta
        trace = sample['paths'][0]['trace']
        player = next(t['pointer'] for t in trace if t['field'] == 'player')
        entity = next(t['pointer'] for t in trace if t['field'] == '<entity>k__BackingField')
        info = m.obj(player)
        if (info['name'], info['namespace']) != ('LocalPlayerComponent', 'XDTLevelAndEntity.Gameplay.Component.Player') \
                or m.ref(player, '<entity>k__BackingField') != entity:
            raise ValueError('移动方向的本地角色归属不符')
        checker = m.ref(player, '_checker')
        ci = m.obj(checker)
        if (ci['name'], ci['namespace']) != ('PlayerSphereChecker', 'XDTLevelAndEntity.Gameplay.Interaction.Checker'):
            raise ValueError('镜头方向检测器类型不符')
        values = {}
        for name in ('_cameraForward', '_playerPos'):
            f = m.field(ci['address'], name)
            vector = m.cls(f['type'])
            if f['code'] != 17 or f['flags'] & 16 or not 16 <= f['offset'] <= ci['size']-12 \
                    or (vector['name'], vector['namespace'], vector['size']) != ('Vector3','UnityEngine',28):
                raise ValueError('镜头方向字段布局不符')
            values[name] = struct.unpack('<3f', m.data(checker+f['offset'],12))
        position = values['_playerPos']
        if any(not math.isfinite(v) for v in position) or math.dist(position, sample['position']) > 2.:
            raise ValueError('镜头检测器与当前角色位置尚未同步')
        basis = camera_basis(values['_cameraForward'])
        if m.ref(player,'_checker') != checker or m.ref(player,'<entity>k__BackingField') != entity \
                or self.locator.snapshot()['paths'][0]['trace'][:2] != trace[:2]:
            raise ValueError('角色或镜头入口在读取中变化')
        return basis
