"""Read the local player's current camera-forward vector without moving them."""
import math
import struct

from utils.drivers.mono_scene import SceneMetadata
from utils.core import thresholds as policy


class DirectionUnavailable(ValueError):
    """Only a typed, synced, near-vertical view can authorize safe fallback probing."""
    def __init__(self, message, safe_to_probe=False):
        super().__init__(message)
        self.safe_to_probe = safe_to_probe


def camera_basis(forward):
    if not isinstance(forward, (tuple, list)) or len(forward) != 3 \
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in forward):
        raise ValueError('镜头方向数据无效')
    length = math.sqrt(sum(v*v for v in forward))
    planar = math.hypot(forward[0], forward[2])
    if abs(length-1.)>policy.COORD_DIRECTION_CAMERA_NORM_TOLERANCE or planar<policy.COORD_DIRECTION_CAMERA_PLANAR_MIN:
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
        players = {t['pointer'] for t in trace if t['field'] == 'player'}
        entities = {t['pointer'] for t in trace if t['field'] == '<entity>k__BackingField'}
        if len(players)!=1 or len(entities)!=1:
            raise DirectionUnavailable('镜头方向的本地角色路径不唯一')
        player,entity = next(iter(players)),next(iter(entities))
        info = m.obj(player)
        if (info['name'], info['namespace']) != ('LocalPlayerComponent', 'XDTLevelAndEntity.Gameplay.Component.Player') \
                or m.ref(player, '<entity>k__BackingField') != entity:
            raise ValueError('移动方向的本地角色归属不符')
        checker = m.ref(player, '_checker')
        ci = m.obj(checker)
        if (ci['name'], ci['namespace']) != ('PlayerSphereChecker', 'XDTLevelAndEntity.Gameplay.Interaction.Checker'):
            raise ValueError('镜头方向检测器类型不符')
        values, raw_values = {}, {}
        for name in ('_cameraForward', '_playerPos'):
            f = m.field(ci['address'], name)
            vector = m.cls(f['type'])
            if f['code'] != 17 or f['flags'] & 16 or not 16 <= f['offset'] <= ci['size']-12 \
                    or (vector['name'], vector['namespace'], vector['size']) != ('Vector3','UnityEngine',28):
                raise ValueError('镜头方向字段布局不符')
            raw_values[name] = (checker+f['offset'],m.data(checker+f['offset'],12))
            values[name] = struct.unpack('<3f',raw_values[name][1])
        position = values['_playerPos']
        if any(not math.isfinite(v) for v in position) or math.dist(position, sample['position']) > policy.COORD_DIRECTION_POSITION_TOLERANCE:
            raise ValueError('镜头检测器与当前角色位置尚未同步')
        if m.ref(player,'_checker') != checker or m.ref(player,'<entity>k__BackingField') != entity \
                or self.locator.snapshot()['paths'][0]['trace'] != trace \
                or any(m.data(at,12)!=raw for at,raw in raw_values.values()):
            raise ValueError('角色或镜头入口在读取中变化')
        forward=values['_cameraForward']
        length=math.sqrt(sum(v*v for v in forward))
        if all(math.isfinite(v) for v in forward) and abs(length-1.)<=policy.COORD_DIRECTION_CAMERA_NORM_TOLERANCE \
                and math.hypot(forward[0],forward[2])<policy.COORD_DIRECTION_CAMERA_PLANAR_MIN:
            raise DirectionUnavailable('镜头接近竖直，暂时无法读取水平朝向',safe_to_probe=True)
        basis = camera_basis(forward)
        return basis
