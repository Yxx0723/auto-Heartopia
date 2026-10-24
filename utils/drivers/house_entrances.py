"""Read numbered house forecourt player positions from the current main config.

fieldNo is the displayed house number; fieldID is a different identifier.
playerPos is the configured standing/arrival position, not the plot centre pos.
No process addresses or guessed offsets are persisted.
"""
import struct
import math

from utils.core.coordinate_route import valid_position
from utils.drivers.global_minerals import GlobalMineralReader
from utils.core.patrol_public_side import exclusion_polygon, public_checkpoint

NAMESPACE = 'EcsClient.XDT.Scene.Shared.Data.ServerData.ResourceSpawnConfig'


class HouseEntranceReader(GlobalMineralReader):
    def vector(self, obj, name):
        m=self.meta;info=m.obj(obj);f=self.field(info,name,17,12);typ=m.cls(f['type'])
        if (typ['name'],typ['namespace'],typ['size']) != ('Vector3','UnityEngine',28):
            raise ValueError('家园位置类型不符')
        raw=m.data(obj+f['offset'],12);p=list(struct.unpack('<3f',raw))
        if not valid_position(p) or m.data(obj+f['offset'],12)!=raw:
            raise ValueError('家园位置正在变化')
        return p

    def public_snapshot(self):
        """Restrict this task to the public side of matched forecourt triggers.

        These are deliberately conservative policy exclusions, not full home meshes.
        """
        m=self.meta;houses=self.snapshot();config=self.cache['config']
        assets=m.ref(config,'TriggerAssets')
        self.expected(assets,'TriggerAssets','EcsClient.XDT.Scene.Shared.Data.ServerData')
        collection=m.ref(assets,'triggers');info=self.expected(collection,'List`1','System.Collections.Generic')
        at=collection+self.field(info,'_version',8,4)['offset'];version=m.integer(at)
        records=tuple(m.list_refs(collection))
        if m.integer(at)!=version:
            raise ValueError('门口触发区正在加载')
        common=m.ref(config,'MapCommonAssets')
        self.expected(common,'MapCommonAssets',NAMESPACE)
        def vector2(name):
            f=self.field(m.obj(common),name,17,8);t=m.cls(f['type'])
            if (t['name'],t['namespace'],t['size'])!=('Vector2','UnityEngine',24):
                raise ValueError('主地图矩形类型不符')
            raw=m.data(common+f['offset'],8);v=struct.unpack('<2f',raw)
            if not all(math.isfinite(x) for x in v) or m.data(common+f['offset'],8)!=raw:
                raise ValueError('主地图矩形正在变化')
            return v
        origin=vector2('mapRectMin');size=vector2('mapSize')
        if not all(1.<x<100000 for x in size):
            raise ValueError('主地图范围无效')
        bounds=(origin[0],origin[1],origin[0]+size[0],origin[1]+size[1])
        signature=(self.cache['context'],self.cache['layout'],assets,collection,version,records,bounds)
        previous=getattr(self,'public_cache',None)
        if previous and previous[0]==signature and self.current_context(self.cache['context']):
            return previous[1]
        by_id={};rows={}
        for obj in self.cache['layout'][2]:
            info=self.expected(obj,'MapFieldPointAsset',NAMESPACE)
            number=m.integer(obj+self.field(info,'fieldNo',8,4)['offset'])
            if 1<=number<=12:
                area=m.integer(obj+self.field(info,'foreyardAreaId',8,4)['offset'])
                if area<=0 or area in by_id:
                    raise ValueError('房门触发区归属不唯一')
                by_id[area]=number;rows[number]=self.vector(obj,'pos')
        polygons={}
        for obj in records:
            info=self.expected(obj,'TriggerAsset','EcsClient.XDT.Scene.Shared.Data.ServerData')
            identity=m.integer(obj+self.field(info,'triggerId',8,4)['offset'])
            if identity not in by_id:
                continue
            f=self.field(info,'polygon',17,16);typ=m.cls(f['type'])
            if (typ['name'],typ['namespace'],typ['size'])!=('Area','',32):
                raise ValueError('门口多边形类型不符')
            pf=m.field(typ['address'],'points')
            if pf['code']!=29 or pf['flags']&16 or pf['offset']!=16:
                raise ValueError('门口多边形点布局不符')
            array=m.pointer(obj+f['offset']+pf['offset']-16);ai,count=m.array(array,max_items=256)
            element=m.cls(ai['element'])
            if (element['name'],element['namespace'],element['size'])!=('Vector3','UnityEngine',28) or not 3<=count<=256:
                raise ValueError('门口多边形点类型或数量不符')
            raw=m.data(array+32,count*12)
            vertices=[list(p) for p in struct.iter_unpack('<3f',raw)]
            if identity in polygons or any(not valid_position(p) for p in vertices) \
                    or m.data(array+32,len(raw))!=raw or m.pointer(obj+f['offset'])!=array:
                raise ValueError('门口多边形读取时变化')
            polygons[identity]=vertices  # Configuration points are already in world space.
        if set(polygons)!=set(by_id) or len(by_id)!=12:
            raise ValueError('12户门口触发区尚未完整确认')
        exclusions=[];directions={}
        result=[dict(row) for row in houses]
        for row in result:
            number=row['house_number'];area=next(a for a,n in by_id.items() if n==number)
            polygon,direction=exclusion_polygon(rows[number],row['position'],polygons[area],bounds)
            exclusions.append(polygon);directions[number]=direction
        for row in result:
            row['public_position']=public_checkpoint(row['position'],directions[row['house_number']],exclusions)
            row['public_source']='forecourt-public-side-policy'
        if m.integer(at)!=version or not self.current_context(self.cache['context']) \
                or m.ref(config,'TriggerAssets')!=assets or self.config_points(config)!=self.cache['layout']:
            raise ValueError('公共侧配置场景已变化')
        value=(result,exclusions);self.public_cache=(signature,value)
        return value

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
