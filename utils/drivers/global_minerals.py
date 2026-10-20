"""Read full main-map resource definitions from registered script objects only."""
import struct
import time

from utils.core.coordinate_route import valid_position
from utils.core import thresholds as policy


_POINT_NS = 'EcsClient.XDT.Scene.Shared.Data.ServerData'


class GlobalMineralReader:
    def __init__(self, meta, mono_base, item_types, clock=time.monotonic, classes=None):
        self.meta, self.base, self.item_types, self.clock = meta, mono_base, item_types, clock
        self.world_class, self.engine_class = classes or (
            self.find_class('XDTBaseService', 'GameWorld', 'XDTGame.Core'),
            self.find_class('ScriptBridge', 'ScriptEngine', 'MonoGame.ScriptFramework'))
        self.cache, self.loads = None, 0

    def find_class(self, assembly, name, namespace):
        meta, node, seen, found = self.meta, self.meta.pointer(self.base+0x4d3880), set(), []
        while node:
            if node in seen or len(seen) >= 512:
                raise ValueError('资源定义程序集正在变化')
            seen.add(node)
            image = meta.pointer(meta.pointer(node)+0x68)
            if meta.text(meta.pointer(image+0x30)) == assembly:
                size, count, buckets = struct.unpack_from('<IIQ', meta.data(image+0x4d0, 40), 24)
                if not 0 < size <= policy.MONO_CLASS_LIMIT or not 0 < count <= policy.MONO_CLASS_LIMIT:
                    raise ValueError('资源定义类型缓存无效')
                seeds = [p for p in struct.unpack('<'+'Q'*size, meta.data(buckets, size*8)) if p]
                if not seeds or meta.pointer(seeds[0]+0x40) != image:
                    raise ValueError('资源定义程序集归属不符')
                found.append(meta.image_class(seeds[0], name, namespace))
            node = meta.pointer(node+8)
        if len(found) != 1:
            raise ValueError('资源定义程序集未加载或存在歧义')
        return found[0]

    def expected(self, obj, name, namespace):
        info = self.meta.obj(obj)
        if (info['name'], info['namespace']) != (name, namespace):
            raise ValueError('全图资源对象类型不符')
        return info

    def field(self, info, name, code, width):
        field = self.meta.field(info['address'], name)
        if field['code'] != code or field['flags'] & 16 or not 16 <= field['offset'] <= info['size']-width:
            raise ValueError('全图资源字段布局不符')
        return field

    def unwrap(self, held):
        info = self.meta.obj(held)
        return self.meta.ref(held, 'ImplInternal') if info['name'] in (
            'DynamicMonoBehaviour', 'DynamicScriptableObject') else held

    def config_points(self, config):
        self.expected(config, 'MainGameLevelConfig', 'XDTDataAndProtocol.Config.GameLevel')
        behaviour = self.meta.ref(config, '_behaviour')
        if self.meta.obj(behaviour)['name'] != 'DynamicMonoBehaviour' or self.meta.ref(behaviour, 'ImplInternal') != config:
            raise ValueError('全图配置未绑定当前脚本对象')
        assets = self.meta.ref(config, 'CollectableResourcePointAssets')
        self.expected(assets, 'CollectableResourcePointAssetsConfig', _POINT_NS)
        points = self.meta.ref(assets, 'points')
        pi = self.expected(points, 'List`1', 'System.Collections.Generic')
        at = points+self.field(pi, '_version', 8, 4)['offset']
        version = self.meta.integer(at)
        records = tuple(self.meta.list_refs(points))
        if self.meta.integer(at) != version:
            raise ValueError('全图资源列表正在变化')
        return assets, points, records, version

    def current_context(self, context):
        node, level, engine, registry = context
        return (self.meta.pointer(self.base+0x4d3878) == self.meta.domain
                and self.meta.static_ref(self.world_class, '_currentLevel') == node
                and self.meta.ref(node, '_instance') == level
                and self.meta.static_ref(self.engine_class, '<Instance>k__BackingField') == engine
                and self.meta.ref(engine, '_holdingObjs') == registry)

    def decode(self, records):
        result, seen = [], set()
        meta = self.meta
        for obj in records:
            info = self.expected(obj, 'CollectableResourcePointAsset', _POINT_NS)
            kind = self.field(info, 'resType', 17, 4)
            enum = meta.cls(kind['type'])
            if (enum['name'], enum['namespace']) != ('ResType', _POINT_NS):
                raise ValueError('资源种类枚举不符')
            if meta.integer(obj+kind['offset']) != 2:
                continue
            item = self.field(info, 'itemTypeId', 8, 4)
            item_id = meta.integer(obj+item['offset'])
            category = self.item_types.get(item_id)
            if category not in ('rock', 'ore'):
                continue
            identity = self.field(info, 'resourceId', 9, 4)
            resource_id = struct.unpack('<I', meta.data(obj+identity['offset'], 4))[0]
            position = self.field(info, 'position', 17, 12)
            vector = meta.cls(position['type'])
            if (vector['name'], vector['namespace']) != ('Vector3', 'UnityEngine'):
                raise ValueError('资源位置类型不符')
            xyz = list(struct.unpack('<3f', meta.data(obj+position['offset'], 12)))
            if not resource_id or resource_id in seen or not valid_position(xyz):
                raise ValueError('全图资源点位无效或重复')
            if meta.integer(obj+item['offset']) != item_id or meta.integer(obj+kind['offset']) != 2:
                raise ValueError('资源定义正在变化')
            seen.add(resource_id)
            result.append({'category': category, 'position': xyz, 'resource_id': resource_id,
                           'item_type_id': item_id, 'map_definition': True,
                           'source': 'main-map-resource-definition'})
        if not result:
            raise ValueError('主地图石矿定义尚未加载')
        return result

    def snapshot(self):
        meta = self.meta
        if meta.pointer(self.base+0x4d3878) != meta.domain:
            raise ValueError('资源运行时已切换')
        node = meta.static_ref(self.world_class, '_currentLevel')
        self.expected(node, 'LevelNode', 'XDTGame.Core')
        level = meta.ref(node, '_instance')
        self.expected(level, 'GameLevel_Main', 'XDTGameSystem.GameLevels')
        engine = meta.static_ref(self.engine_class, '<Instance>k__BackingField')
        self.expected(engine, 'ScriptEngine', 'MonoGame.ScriptFramework')
        registry = meta.ref(engine, '_holdingObjs')
        held_objects = meta.dictionary(registry)
        context, now = (node, level, engine, registry), self.clock()
        cached = self.cache
        if cached and cached['context'] == context and cached['held'] in held_objects:
            if self.unwrap(cached['held']) == cached['config']:
                layout = self.config_points(cached['config'])
                if layout == cached['layout'] and now-cached['at'] < policy.COORD_SCENE_CACHE_FAR_SECONDS \
                        and self.current_context(context):
                    return cached['points']
        self.cache = None
        candidates = {}
        for held in held_objects:
            try:
                config = self.unwrap(held)
                info = meta.obj(config)
                if (info['name'], info['namespace']) == ('MainGameLevelConfig', 'XDTDataAndProtocol.Config.GameLevel'):
                    candidates[config] = held
            except ValueError:
                continue  # Other registered wrappers may be unloading independently.
        valid = []
        for config, held in candidates.items():
            try:
                layout = self.config_points(config)
                points = self.decode(layout[2])
                if self.config_points(config) != layout or self.unwrap(held) != config:
                    raise ValueError('主地图资源列表正在变化')
                valid.append((config, held, layout, points))
            except ValueError:
                continue
        if len(valid) != 1 or not self.current_context(context):
            raise ValueError('主地图资源定义未加载或存在歧义')
        config, held, layout, points = valid[0]
        self.loads += 1
        self.cache = {'context': context, 'held': held, 'config': config, 'layout': layout,
                      'points': points, 'at': now}
        return points
