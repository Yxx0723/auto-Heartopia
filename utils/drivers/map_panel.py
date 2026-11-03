"""Owned open-map observations. Read only; no UI calls or heap scanning.

Managed observations alone do not permit input. The separately pinned native
layout supplies projection; UI actions still require target and route readback.
"""
import hashlib
import json
import math
import struct
import time

from utils.drivers.global_minerals import GlobalMineralReader
from utils.drivers.mono_scene import SceneMetadata
from utils.drivers.native_guidance import NativeGuidanceReader
from utils.core.map_targeting import MapView
from utils.core.ui_projection import rect_to_client


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

    def ui_views(self):
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
        return (services,manager,layers,tuple(views))

    def owner(self):
        m=self.meta;services,manager,layers,views=self.ui_views()
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

    def map_control(self,client_size,action):
        """Read owned map-open / map-back buttons; M is not a toggle contract."""
        from utils.drivers.native_ui_layout import NativeUiLayout
        if action not in ('open','close'):
            raise ValueError('地图开关操作无效')
        m=self.meta;scene=self.scene_context();scope=self.ui_views()
        layout=NativeUiLayout(self.memory,m)
        if action=='open':
            if self.owner() is not None:
                return {'state':'closed','kind':'map_open','captured_at':self.clock()}
            panels=[v for v in scope[3] if (m.obj(v)['name'],m.obj(v)['namespace'])
                    ==('MapHudPanel','XDTGame.UI.Panel')]
            if len(panels)!=1:
                raise ValueError('小地图入口不唯一或尚未显示')
            panel=panels[0];nodes=m.ref(panel,'ui')
            self.expected(nodes,'MapHudPanel_Auto','XDTGame.Auto')
            root=m.ref(nodes,'map_root_go');bar=m.ref(nodes,'map_bar_go')
            if not layout.active(root) or not layout.active(bar):
                raise ValueError('小地图入口尚未显示')
            widget=m.ref(nodes,'map_bar_widget')
            self.expected(widget,'CommonMapBar','XDTGame.UI.Widget')
            ui=m.ref(widget,'ui');self.expected(ui,'CommonMapBar_Auto','XDTGame.Auto')
            component=m.ref(ui,'map_btn')
        else:
            owner=self.owner()
            if owner is None:
                return {'state':'closed','kind':'map_close','captured_at':self.clock()}
            panel,nodes=owner[3:];root=bar=None
            widget=m.ref(nodes,'close_widget')
            self.expected(widget,'CommonTitleWidget','XDTGame.UI.Widget')
            if m.ref(widget,'_thisView')!=panel or m.ref(widget,'_backHandler'):
                raise ValueError('地图返回按钮未绑定默认关闭操作')
            ui=m.ref(widget,'ui');self.expected(ui,'CommonTitleWidget_Auto','XDTGame.Auto')
            component=m.ref(ui,'back_btn')
        owner=(*scope[:3],panel,nodes)
        rect=self._button_rect(layout,owner,component,client_size)
        if self.ui_views()!=scope or self.scene_context()!=scene or m.ref(panel,'ui')!=nodes \
                or m.ref(widget,'ui')!=ui:
            raise ValueError('地图开关按钮的界面归属正在变化')
        if action=='open':
            if m.ref(nodes,'map_bar_widget')!=widget or m.ref(ui,'map_btn')!=component \
                    or not layout.active(root) or not layout.active(bar) or self.owner() is not None:
                raise ValueError('小地图入口正在变化')
        elif m.ref(nodes,'close_widget')!=widget or m.ref(ui,'back_btn')!=component \
                or m.ref(widget,'_thisView')!=panel or m.ref(widget,'_backHandler'):
            raise ValueError('地图返回按钮正在变化')
        identity=hashlib.sha256(json.dumps([scene,owner,widget,ui,component]).encode()).hexdigest()
        return {'state':'open','kind':'map_'+action,'buttons':{action:rect},
                'scene_context':scene,'view_identity':identity,'captured_at':self.clock()}

    def scene_context(self):
        domain=self.meta.pointer(self.base+0x4d3878)
        if domain!=self.meta.domain:
            raise ValueError('地图运行域已改变')
        world=self.guidance._owner()
        result=hashlib.sha256(json.dumps([self.memory.profile['game_assembly_sha256'],
            self.memory.pid,domain,world[0]]).encode()).hexdigest()
        if self.guidance._owner()!=world or self.meta.pointer(self.base+0x4d3878)!=domain:
            raise ValueError('地图场景正在变化')
        return result

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

    def geometry(self,client_size):
        """Source-verified affine layout; a subsequent UI target still needs readback.

        Canvas and sketch data use the actual native accessors' storage. Two
        matching managed observations bracket all layout reads. This does not
        report that an automatic UI action or native route has succeeded.
        """
        from utils.drivers.native_ui_layout import NativeUiLayout
        first=self.snapshot();owner=self.owner()
        if first['state']!='projection_unverified' or owner is None or first['other_region_view']:
            raise ValueError('需要打开当前所在区域的大地图')
        if not isinstance(client_size,(tuple,list)) or len(client_size)!=2 \
                or any(type(x) is not int or not 64<=x<=16384 for x in client_size):
            raise ValueError('游戏客户区尺寸无效')
        layout=NativeUiLayout(self.memory,self.meta)
        canvas_obj=self.meta.ref(owner[1],'<CanvasRect>k__BackingField')
        sketch_obj=self.meta.ref(owner[4],'sketch_t')
        canvas=layout.rect(canvas_obj);sketch=layout.rect(sketch_obj)
        sketch_chain=layout.transforms(sketch_obj)
        scale=sketch_chain[0][2]
        rotation=sketch_chain[0][1]
        rect=canvas['rect'];cw,ch=rect[2:]
        close=lambda a,b:math.isclose(a,b,rel_tol=1e-6,abs_tol=.01)
        if not close(rect[0],-cw/2) or not close(rect[1],-ch/2) \
                or any(not close(a,b*2) for a,b in zip((cw,ch),first['canvas_half_size'])) \
                or not close(scale[0],first['map_scale']) or not close(scale[1],scale[0]) \
                or any(not close(a,b) for a,b in zip(rotation,(0.,0.,0.,1.))):
            raise ValueError('地图实际画布、缩放或旋转与界面模型不符')
        last=self.snapshot()
        if {k:v for k,v in first.items() if k!='captured_at'} \
                !={k:v for k,v in last.items() if k!='captured_at'} or self.owner()!=owner:
            raise ValueError('地图布局读取期间视图发生变化')
        w,h=client_size
        # Exclude persistent top/bottom controls and the left zoom strip.
        # This is a conservative interaction ROI, not map/world coverage.
        return MapView(first['scene_context'],first['view_identity'],tuple(client_size),
            (cw,ch),sketch['anchored_position'],scale[0],first['map_ratio'],self.clock(),
            True,True,(w*.12,h*.16,w*.98,h*.90))

    def _button_rect(self,layout,owner,component,client_size):
        canvas=self.meta.ref(owner[1],'<CanvasRect>k__BackingField')
        rect,chain=layout.button_geometry(component)
        result=rect_to_client(rect,chain,layout.rect(canvas)['rect'],
                              layout.transforms(canvas),client_size)
        l,t,r,b=result;w,h=client_size
        if not 0<=l<r<=w or not 0<=t<b<=h:
            raise ValueError('地图按钮未完整位于游戏窗口内')
        return result

    def zoom_state(self,client_size):
        from utils.drivers.native_ui_layout import NativeUiLayout
        before=self.snapshot();owner=self.owner()
        if owner is None or before['other_region_view']:
            raise ValueError('最低缩放需要当前区域大地图')
        layout=NativeUiLayout(self.memory,self.meta);m=self.meta;n=layout.native
        wrapper=m.ref(owner[4],'scale_slider');native=layout.resolve(wrapper,'Slider','UnityEngine.UI')
        info=n.obj(native);fields=[n.field(info['address'],key,12) for key in ('m_Value','m_MinValue','m_MaxValue')]
        raw=tuple(n.data(native+f['offset'],4) for f in fields)
        value,low,high=(struct.unpack('<f',v)[0] for v in raw)
        if not all(math.isfinite(v) for v in (value,low,high)) or not low<=value<=high or high<=low:
            raise ValueError('地图缩放滑条范围无效')
        component=m.ref(owner[4],'decrease_scale_btn')
        buttons={} if (value-low)/(high-low)<=1e-6 else {'decrease':self._button_rect(layout,owner,component,client_size)}
        after=self.snapshot()
        if raw!=tuple(n.data(native+f['offset'],4) for f in fields) or self.owner()!=owner \
                or layout.resolve(wrapper,'Slider','UnityEngine.UI')!=native \
                or m.ref(owner[4],'scale_slider')!=wrapper or m.ref(owner[4],'decrease_scale_btn')!=component \
                or any(before[k]!=after[k] for k in ('scene_context','view_identity','map_scale')):
            raise ValueError('地图缩放滑条正在变化')
        return dict(state='open',kind='map_zoom',fraction=(value-low)/(high-low),
            scene_context=before['scene_context'],view_identity=before['view_identity'],
            buttons=buttons,captured_at=self.clock())

    def overlays(self,client_size):
        """Recognized map popups and blank candidates avoiding current icons."""
        from utils.drivers.native_ui_layout import NativeUiLayout
        from utils.core.map_targeting import blank_map_points
        view=self.geometry(client_size);owner=self.owner();m=self.meta
        if owner is None:raise ValueError('地图已关闭')
        layout=NativeUiLayout(self.memory,m);canvas=m.ref(owner[1],'<CanvasRect>k__BackingField')
        cr=layout.rect(canvas)['rect'];chain=layout.transforms(canvas);rects=[];popups=[]
        def projected(root):
            return rect_to_client(layout.rect(root)['rect'],layout.transforms(root),cr,chain,client_size)
        visible=[]
        for name in ('dialog_customspot','dialog','spotClickedList'):
            go=m.ref(owner[4],name+'_go');active=layout.active(go);visible.append((go,active))
            if active:
                widget=m.ref(owner[4],name+'_widget');ui=m.ref(widget,'ui')
                rects.append(projected(m.ref(ui,'root_t')));popups.append(name)
        if popups:
            linked=m.ref(owner[3],'_spotWidgets')
            self.expected(linked,'LinkedList`1','System.Collections.Generic')
            head=m.ref(linked,'head');count=struct.unpack('<i',self.payload(linked,'count',8,4))[0]
            version=self.payload(linked,'version',8,4);node=head;seen=set()
            if not 0<=count<=4096 or bool(head)!=bool(count):raise ValueError('地图图标链表无效')
            for _ in range(count):
                if not node or node in seen or m.ref(node,'list')!=linked:raise ValueError('地图图标归属变化')
                seen.add(node);widget=m.ref(node,'item')
                self.expected(widget,'MapSpotWidget','XDTGame.UI.Widget')
                rects.append(projected(m.ref(m.ref(widget,'ui'),'root_t')));node=m.ref(node,'next')
            if node!=head or self.payload(linked,'version',8,4)!=version \
                    or m.ref(owner[3],'_spotWidgets')!=linked:
                raise ValueError('地图图标集合正在变化')
        current=self.geometry(client_size)
        if current.signature()!=view.signature() or self.owner()!=owner \
                or any(layout.active(go)!=active for go,active in visible):
            raise ValueError('地图弹窗或位置正在变化')
        points=blank_map_points(view,tuple(rects),self.clock(),max_age=1.,margin=6.) if popups else ()
        return dict(popups=tuple(popups),blank_points=points)

    def dialog(self,client_size,kind='add'):
        """Owned visible dialog only. Never treats a pooled widget as visible."""
        from utils.drivers.native_ui_layout import NativeUiLayout
        if kind not in ('add','spot'):
            raise ValueError('地图弹窗类型无效')
        before=self.snapshot();owner=self.owner()
        if owner is None or before['state']!='projection_unverified' or before['other_region_view']:
            raise ValueError('地图视图未确认')
        layout=NativeUiLayout(self.memory,self.meta);m=self.meta;nodes=owner[4]
        prefix='dialog_customspot' if kind=='add' else 'dialog'
        root=m.ref(nodes,prefix+'_go')
        if not layout.active(root):
            return {'state':'closed','kind':kind,'captured_at':self.clock()}
        widget=m.ref(nodes,prefix+'_widget');ui=m.ref(widget,'ui')
        self.expected(widget,'DialogCustomSpotWidget' if kind=='add' else 'DialogWidget','XDTGame.UI.Widget')
        def identity():
            if kind=='add':
                return (None,struct.unpack('<I',self.payload(widget,'<NetID>k__BackingField',9,4))[0],7)
            spot=m.ref(widget,'<SpotData>k__BackingField')
            self.expected(spot,'MapSpot','XDTGameSystem.GameplaySystem.MapSpots')
            return (spot,struct.unpack('<I',self.payload(spot,'usageId',8,4))[0],
                    struct.unpack('<i',self.payload(spot,'category',17,4))[0])
        observed=identity();_,usage_id,category=observed
        position=self.vector(owner[3],'currentSpotPos')
        buttons={'save':'save_swapbtn','cancel':'cancel_swapbtn'} if kind=='add' \
            else {'track':'track_toggleBtn','delete':'delete_swapbtn'}
        rects={key:self._button_rect(layout,owner,m.ref(ui,name),client_size)
               for key,name in buttons.items()}
        if not layout.active(root) or self.owner()!=owner or identity()!=observed \
                or m.ref(nodes,prefix+'_widget')!=widget or m.ref(widget,'ui')!=ui \
                or self.vector(owner[3],'currentSpotPos')!=position \
                or self.snapshot()['scene_context']!=before['scene_context']:
            raise ValueError('地图弹窗或其场景归属正在变化')
        return {'state':'open','kind':kind,'usage_id':str(usage_id),'category':category,
            'position_xz':position,'buttons':rects,
            'scene_context':before['scene_context'],'view_identity':before['view_identity'],
            'captured_at':self.clock()}

    def custom_markers(self):
        """Current owned map widgets, including markers added after map startup.

        MapSystem's initial list can omit newly saved markers. Custom MapSpot
        GetPosition also queries protocol data, so its cached _position is not
        authoritative after edits. Read the displayed root's anchored XZ (the
        source uses world XZ * MapRatio), bracketed by graph/layout checks. Y=0
        is map-only, not a ground height or a replacement for saved point Y.
        """
        from utils.drivers.native_ui_layout import NativeUiLayout
        m=self.meta;before=self.snapshot();owner=self.owner()
        if owner is None or before['state']!='projection_unverified' or before['other_region_view']:
            raise ValueError('地图所在区域尚未确认')
        ratio=before['map_ratio']
        if not math.isfinite(ratio) or ratio<=0:
            raise ValueError('地图标记比例无效')
        layout=NativeUiLayout(self.memory,m)
        linked=m.ref(owner[3],'_spotWidgets')
        self.expected(linked,'LinkedList`1','System.Collections.Generic')
        def stamp():
            return (m.ref(linked,'head'),struct.unpack('<i',self.payload(linked,'count',8,4))[0],
                    self.payload(linked,'version',8,4))
        initial=stamp();head,count,_=initial
        # Bounded owned traversal, not a search of the process heap.
        if not 0<=count<=4096 or bool(head)!=bool(count):
            raise ValueError('地图标记链表范围无效')
        result={};node=head;seen=set();links=[];widgets=[]
        for _ in range(count):
            if not node or node in seen:
                raise ValueError('地图标记链表断开或提前循环')
            seen.add(node);self.expected(node,'LinkedListNode`1','System.Collections.Generic')
            item=m.ref(node,'item');next_node=m.ref(node,'next');previous=m.ref(node,'prev')
            if m.ref(node,'list')!=linked or not next_node or not previous \
                    or m.ref(next_node,'prev')!=node or m.ref(previous,'next')!=node:
                raise ValueError('地图标记链表归属不符')
            links.append((node,item,next_node,previous))
            self.expected(item,'MapSpotWidget','XDTGame.UI.Widget')
            spot=m.ref(item,'<selfData>k__BackingField')
            self.expected(spot,'MapSpot','XDTGameSystem.GameplaySystem.MapSpots')
            category=struct.unpack('<i',self.payload(spot,'category',17,4))[0]
            if category==7:
                raw_id=self.payload(spot,'usageId',8,4);usage_id=struct.unpack('<I',raw_id)[0]
                ui=m.ref(item,'ui');self.expected(ui,'MapSpotWidget_Auto','XDTGame.Auto')
                root=m.ref(ui,'root_t');anchor=layout.rect(root)['anchored_position']
                if not usage_id or str(usage_id) in result or not all(math.isfinite(v) for v in anchor):
                    raise ValueError('自定义地图标记的身份或位置无效')
                result[str(usage_id)]=(anchor[0]/ratio,0.,anchor[1]/ratio)
                widgets.append((item,spot,raw_id,ui,root,anchor))
            node=next_node
        if node!=head or stamp()!=initial or m.ref(owner[3],'_spotWidgets')!=linked:
            raise ValueError('地图标记链表正在变化')
        for node,item,next_node,previous in links:
            if (m.ref(node,'item'),m.ref(node,'next'),m.ref(node,'prev'),m.ref(node,'list')) \
                    !=(item,next_node,previous,linked):
                raise ValueError('地图标记链表正在变化')
        for item,spot,raw_id,ui,root,anchor in widgets:
            if m.ref(item,'<selfData>k__BackingField')!=spot \
                    or self.payload(spot,'usageId',8,4)!=raw_id \
                    or self.payload(spot,'category',17,4)!=struct.pack('<i',7) \
                    or m.ref(item,'ui')!=ui or m.ref(ui,'root_t')!=root \
                    or layout.rect(root)['anchored_position']!=anchor:
                raise ValueError('地图标记目标或其位置正在变化')
        after=self.snapshot()
        if self.owner()!=owner or any(before[k]!=after[k] for k in
                ('scene_context','view_identity','map_ratio','other_region_view')):
            raise ValueError('地图标记所在视图正在变化')
        return result



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
