"""Owned open-map observations. Read only; no UI calls or heap scanning.

These managed fields establish map identity and read back a held map position.
Native RectTransform-to-screen projection is NOT yet verified; callers cannot
use this observation as permission to click or start navigation.
"""
import hashlib
import json
import math
import struct
import time

from utils.drivers.global_minerals import GlobalMineralReader
from utils.drivers.mono_scene import SceneMetadata
from utils.drivers.native_guidance import NativeGuidanceReader


class MapPanelReader:
    def __init__(self, memory, clock=time.perf_counter):
        if memory.locator is None:
            raise ValueError('当前版本不支持地图状态读取')
        self.memory,self.clock=memory,clock
        self.meta=SceneMetadata(memory._read,memory.locator.domain)
        self.base=memory.base
        self.guidance=NativeGuidanceReader(memory,clock)
        self.classes={}

    def find_class(self, assembly, name, namespace):
        key=(assembly,name,namespace)
        if key not in self.classes:
            self.classes[key]=GlobalMineralReader.find_class(self,assembly,name,namespace)
        return self.classes[key]

    def expected(self, obj, name, namespace):
        info=self.meta.obj(obj)
        if (info['name'],info['namespace'])!=(name,namespace):
            raise ValueError('地图对象类型或归属未确认')
        return info

    def payload(self, obj, name, code, width):
        m=self.meta;info=m.obj(obj);field=m.field(info['address'],name)
        if field['code']!=code or field['flags']&16 or not 16<=field['offset']<=info['size']-width:
            raise ValueError('地图字段类型或范围无效')
        return m.data(obj+field['offset'],width)

    def vector(self, obj, name):
        info=self.meta.obj(obj);field=self.meta.field(info['address'],name)
        typ=self.meta.cls(field['type'])
        if (typ['name'],typ['namespace'],typ['size'])!=('Vector2','UnityEngine',24):
            raise ValueError('地图坐标字段不是已确认的二维向量')
        value=struct.unpack('<2f',self.payload(obj,name,17,8))
        if not all(math.isfinite(x) for x in value):
            raise ValueError('地图坐标数值无效')
        return value

    def static_instance(self, klass):
        seen=set()
        while klass and klass not in seen and len(seen)<32:
            seen.add(klass);info=self.meta.cls(klass)
            if '_instance' in info['fields']:
                return self.meta.static_ref(klass,'_instance')
            klass=info['parent']
        raise ValueError('地图模块入口未确认')

    def region_context(self, panel):
        context=self.meta.ref(panel,'_viewContext')
        if not context:
            return (0,False)
        self.expected(context,'MapViewContext','XDTGame.UI.Panel')
        current=self.payload(context,'IsCurrentRegion',2,1)
        if current not in (b'\0',b'\x01'):
            raise ValueError('地图区域状态无效')
        return (context,current==b'\0')

    def owner(self):
        m=self.meta
        managers=self.find_class('XDTBaseService','Managers','XDTGame.Framework')
        services=m.static_ref(managers,'_serviceDic')
        matches=[]
        for item in m.dictionary(services):
            manager=m.ref(item,'manager');info=m.obj(manager)
            if (info['name'],info['namespace'])==('UIManager','XDTGame.Core'):
                matches.append(manager)
        if len(matches)!=1:
            raise ValueError('界面管理器入口不唯一')
        manager=matches[0];layers=m.ref(manager,'_viewLayerDic');views=[]
        for layer in m.dictionary(layers):
            views.extend(m.list_refs(m.ref(layer,'stack')))
        panels=[]
        for obj in views:
            info=m.obj(obj)
            if (info['name'],info['namespace'])==('MapPanel','XDTGame.UI.Panel'):
                panels.append(obj)
        if len(panels)>1:
            raise ValueError('已打开的地图面板不唯一')
        if not panels:
            return None
        panel=panels[0];nodes=m.ref(panel,'ui')
        self.expected(nodes,'MapPanel_Auto','XDTGame.Auto')
        return (services,manager,layers,panel,nodes)

    def snapshot(self):
        m=self.meta
        domain=m.pointer(self.base+0x4d3878)
        if domain!=m.domain:
            raise ValueError('游戏运行域已改变')
        world=self.guidance._owner()
        owner=self.owner()
        if owner is None:
            return {'state':'closed','projection_verified':False,'read_only':True,
                    'input_sent':False,'captured_at':self.clock()}
        panel=owner[3]
        klass=self.find_class('XDTGameSystem','MapSystem','XDTGameSystem.GameplaySystem.MapSpots')
        module=self.static_instance(klass)
        self.expected(module,'MapSystem','XDTGameSystem.GameplaySystem.MapSpots')
        ratio_field=m.field(klass,'MapRatio')
        if ratio_field['code']!=12 or not ratio_field['flags']&16:
            raise ValueError('地图比例字段未确认')
        vtable=m.pointer(klass+0xd8)
        if m.pointer(vtable)!=klass or m.pointer(vtable+16)!=m.domain:
            raise ValueError('地图比例字段运行域不符')
        slots=m.integer(klass+0x5c)
        if not 0<=slots<4096:
            raise ValueError('地图比例字段存储无效')
        storage=m.pointer(vtable+0x50+slots*8)

        def observe():
            return (self.payload(panel,'_mapSpotsInited',2,1),self.vector(panel,'_center'),
                    self.vector(panel,'currentSpotPos'),self.payload(module,'_currentScale',12,4),
                    m.data(storage+ratio_field['offset'],4),self.region_context(panel))

        first=observe()
        if first[0]!=b'\x01':
            raise ValueError('地图尚未初始化')
        scale,ratio=struct.unpack('<f',first[3])[0],struct.unpack('<f',first[4])[0]
        if not all(math.isfinite(x) and x>0 for x in (*first[1],scale,ratio)):
            raise ValueError('地图画布或缩放参数无效')
        if observe()!=first or self.owner()!=owner or self.guidance._owner()!=world \
                or self.static_instance(klass)!=module or m.pointer(self.base+0x4d3878)!=domain:
            raise ValueError('地图视图或场景正在变化')
        scene=hashlib.sha256(json.dumps([self.memory.profile['game_assembly_sha256'],
            self.memory.pid,domain,world[0]]).encode()).hexdigest()
        identity=hashlib.sha256(json.dumps([scene,owner,module]).encode()).hexdigest()
        return {'state':'projection_unverified','read_only':True,'input_sent':False,
                'projection_verified':False,'scene_context':scene,'view_identity':identity,
                'canvas_half_size':first[1],'current_spot_xz':first[2],
                'map_scale':scale,'map_ratio':ratio,'other_region_view':first[5][1],
                'captured_at':self.clock()}


def probe_map_panel(profile):
    """Read-only preflight; deliberately does not claim targeting is ready."""
    from utils.drivers.coordinate_memory import ReadOnlyPosition
    from utils.drivers.coordinate_process import find_game_processes
    pids=find_game_processes()
    if len(pids)!=1:
        raise ValueError('地图预检需要唯一游戏进程')
    memory=ReadOnlyPosition(pids[0],profile)
    try:
        reader=MapPanelReader(memory)
        samples=[reader.snapshot()]
        time.sleep(.05)
        samples.append(reader.snapshot())
        return {'state':'observed','read_only':True,'input_sent':False,'samples':samples,
                'automatic_targeting_ready':False}
    finally:
        memory.close()
