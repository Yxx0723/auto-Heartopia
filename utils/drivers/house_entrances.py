"""Read numbered house forecourt player positions from the current main config.

fieldNo is the displayed house number; fieldID is a different identifier.
playerPos is the configured standing/arrival position, not the plot centre pos.
No process addresses or guessed offsets are persisted.
"""
import struct

from utils.core.coordinate_route import valid_position
from utils.drivers.global_minerals import GlobalMineralReader

NAMESPACE = 'EcsClient.XDT.Scene.Shared.Data.ServerData.ResourceSpawnConfig'


class HouseEntranceReader(GlobalMineralReader):
    def config_points(self, config):
        m = self.meta
        self.expected(config, 'MainGameLevelConfig', 'XDTDataAndProtocol.Config.GameLevel')
        behaviour = m.ref(config, '_behaviour')
        if m.obj(behaviour)['name'] != 'DynamicMonoBehaviour' or m.ref(behaviour, 'ImplInternal') != config:
            raise ValueError('房门配置未绑定当前主地图')
        assets = m.ref(config, 'MapFieldPointsAsset')
        self.expected(assets, 'MapFieldPointsAsset', NAMESPACE)
        points = m.ref(assets, 'fieldPoints')
        info = self.expected(points, 'List`1', 'System.Collections.Generic')
        at = points + self.field(info, '_version', 8, 4)['offset']
        version = m.integer(at)
        records = tuple(m.list_refs(points))
        if m.integer(at) != version:
            raise ValueError('房门列表正在变化')
        return assets, points, records, version

    def decode(self, records):
        m, result, ids = self.meta, {}, set()
        for obj in records:
            info = self.expected(obj, 'MapFieldPointAsset', NAMESPACE)
            number_at = obj + self.field(info, 'fieldNo', 8, 4)['offset']
            number = m.integer(number_at)
            if not 1 <= number <= 12:
                continue
            identity = m.integer(obj + self.field(info, 'fieldID', 8, 4)['offset'])
            field = self.field(info, 'playerPos', 17, 12)
            vector = m.cls(field['type'])
            if (vector['name'], vector['namespace'], vector['size']) != ('Vector3', 'UnityEngine', 28):
                raise ValueError('房门坐标类型不符')
            raw = m.data(obj + field['offset'], 12)
            position = list(struct.unpack('<3f', raw))
            if number in result or identity <= 0 or identity in ids or not valid_position(position) \
                    or position == [0., 0., 0.]:
                raise ValueError('房门编号或入口坐标无效')
            if m.integer(number_at) != number or m.data(obj + field['offset'], 12) != raw:
                raise ValueError('房门配置正在变化')
            ids.add(identity)
            result[number] = {'house_number': number, 'field_id': identity, 'position': position,
                              'source': 'main-map-house-player-position'}
        if set(result) != set(range(1, 13)):
            raise ValueError('1～12号房门入口尚未完整读取')
        return [result[number] for number in range(1, 13)]
