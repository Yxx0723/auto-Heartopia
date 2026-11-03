"""Owned map graph and observation failures, using synthetic metadata."""
from unittest.mock import Mock
import struct

import pytest

from utils.drivers.map_panel import MapPanelReader


def reader():
    r=MapPanelReader.__new__(MapPanelReader);m=r.meta=Mock()
    r.base=100000;r.clock=lambda:20.;r.classes={}
    r.memory=Mock(pid=123,profile={'game_assembly_sha256':'a'*64})
    r.guidance=Mock();r.guidance._owner.return_value=(10,11,12)
    m.domain=1
    classes={1001:dict(address=1001,name='MapSystem',namespace='XDTGameSystem.GameplaySystem.MapSpots',size=128,
                       fields={},parent=1101),
             1101:dict(address=1101,fields={'_instance':{}},parent=0),
             8001:dict(address=8001,name='Vector2',namespace='UnityEngine',size=24,fields={},parent=0)}
    objects={200:dict(address=5001,name='UIManager',namespace='XDTGame.Core',size=256),
             300:dict(address=6001,name='MapPanel',namespace='XDTGame.UI.Panel',size=288),
             301:dict(address=6002,name='MapPanel_Auto',namespace='XDTGame.Auto',size=584),
             400:classes[1001]}
    fields={'_mapSpotsInited':dict(code=2,offset=20,flags=1),
            '_center':dict(code=17,offset=40,flags=1,type=8001),
            'currentSpotPos':dict(code=17,offset=48,flags=1,type=8001),
            '_currentScale':dict(code=12,offset=24,flags=1),
            'MapRatio':dict(code=12,offset=0,flags=17)}
    m.obj.side_effect=lambda at:objects.get(at,dict(address=9001,name='service',namespace='',size=64))
    m.cls.side_effect=lambda at:classes[at]
    m.field.side_effect=lambda cls,name:fields[name]
    pointers={r.base+0x4d3878:1,1001+0xd8:2000,2000:1001,2016:1,2080:3000}
    m.pointer.side_effect=lambda at:pointers[at]
    m.integer.return_value=0
    statics={(7001,'_serviceDic'):100,(1101,'_instance'):400}
    m.static_ref.side_effect=lambda cls,name:statics[cls,name]
    refs={(101,'manager'):200,(200,'_viewLayerDic'):210,(211,'stack'):220,
          (300,'ui'):301,(300,'_viewContext'):0}
    m.ref.side_effect=lambda obj,name:refs[obj,name]
    m.dictionary.side_effect=lambda at:{100:[101],210:[211]}[at]
    m.list_refs.return_value=[300]
    payloads={320:b'\x01',340:struct.pack('<2f',960.,540.),348:struct.pack('<2f',25.,-40.),
              424:struct.pack('<f',1.19),3000:struct.pack('<f',5.)}
    m.data.side_effect=lambda at,n:payloads[at]
    r.find_class=Mock(side_effect=lambda assembly,name,ns:7001 if name=='Managers' else 1001)
    return r,m,payloads


def test_open_map_is_observed_without_claiming_projection_or_targeting_ready():
    r,m,_=reader();d=r.snapshot()
    assert d['state']=='projection_unverified' and d['projection_verified'] is False
    assert d['read_only'] and not d['input_sent']
    assert d['current_spot_xz']==(25.,-40.)
    assert d['map_scale']==pytest.approx(1.19)
    assert d['map_ratio']==5. and d['captured_at']==20.
    assert len(d['scene_context'])==64 and len(d['view_identity'])==64


def test_closed_map_never_uses_old_instance_from_heap_or_cache():
    r,m,_=reader();m.list_refs.return_value=[]
    assert r.snapshot()['state']=='closed'


@pytest.mark.parametrize('fault',['duplicate','type','uninitialized','domain','scene_change',
    'panel_change','zero_scale','nan_ratio','wrong_vector_type'])
def test_ambiguous_or_changing_ui_fails_closed(fault):
    r,m,p=reader()
    if fault=='duplicate':m.list_refs.return_value=[300,300]
    elif fault=='type':
        original=m.obj.side_effect
        m.obj.side_effect=lambda at:dict(address=6002,name='WrongPanel',namespace='XDTGame.Auto',size=584) if at==301 else original(at)
    elif fault=='uninitialized':p[320]=b'\0'
    elif fault=='domain':
        original=m.pointer.side_effect
        m.pointer.side_effect=lambda at:2 if at==r.base+0x4d3878 else original(at)
    elif fault=='scene_change':r.guidance._owner.side_effect=[(10,11,12),(20,21,22)]
    elif fault=='panel_change':r.owner=Mock(side_effect=[(100,200,210,300,301),(100,200,210,302,303)])
    elif fault=='zero_scale':p[424]=struct.pack('<f',0.)
    elif fault=='nan_ratio':p[3000]=struct.pack('<f',float('nan'))
    else:
        original=m.cls.side_effect
        m.cls.side_effect=lambda at:dict(name='Vector3',namespace='UnityEngine',size=28) if at==8001 else original(at)
    with pytest.raises(ValueError):r.snapshot()


def test_held_map_position_must_not_change_during_observation():
    r,m,p=reader();calls=[]
    def read(at,n):
        if at==348:
            calls.append(1)
            return struct.pack('<2f',len(calls),-40.)
        return p[at]
    m.data.side_effect=read
    with pytest.raises(ValueError):r.snapshot()


def test_map_module_change_invalidates_the_readback():
    r,m,_=reader();r.static_instance=Mock(side_effect=[400,401])
    with pytest.raises(ValueError):r.snapshot()


@pytest.mark.parametrize('current',[True,False])
def test_non_null_view_context_is_not_necessarily_another_region(current):
    r,m,p=reader()
    original_obj=m.obj.side_effect;original_ref=m.ref.side_effect;original_field=m.field.side_effect
    m.obj.side_effect=lambda at:dict(address=7002,name='MapViewContext',namespace='XDTGame.UI.Panel',size=28) if at==500 else original_obj(at)
    m.ref.side_effect=lambda obj,name:500 if (obj,name)==(300,'_viewContext') else original_ref(obj,name)
    m.field.side_effect=lambda cls,name:dict(code=2,offset=20,flags=6) if (cls,name)==(7002,'IsCurrentRegion') else original_field(cls,name)
    p[520]=bytes([current])
    assert r.snapshot()['other_region_view'] is (not current)


def test_closed_and_unknown_region_context_never_inherit_a_previous_region():
    r,m,p=reader();original=m.ref.side_effect
    m.ref.side_effect=lambda obj,name:500 if (obj,name)==(300,'_viewContext') else original(obj,name)
    with pytest.raises(ValueError):r.snapshot()


@pytest.mark.parametrize('fault',[None,'canvas_origin','scale','rotation','other_region'])
def test_actual_native_layout_must_match_the_current_map_model(monkeypatch,fault):
    r,m,_=reader();original=m.ref.side_effect
    m.ref.side_effect=lambda obj,name:600 if name=='<CanvasRect>k__BackingField' else 601 if name=='sketch_t' else original(obj,name)
    native=Mock();canvas=(-960.,-540.,1920.,1080.)
    scale=1.19;rot=(0.,0.,0.,1.)
    if fault=='canvas_origin':canvas=(0.,0.,1920.,1080.)
    elif fault=='scale':scale=1.3
    elif fault=='rotation':rot=(0.,0.,1.,0.)
    elif fault=='other_region':r.region_context=Mock(return_value=(500,True))
    native.rect.side_effect=lambda obj:dict(rect=canvas,anchored_position=(100.,200.))
    native.transforms.return_value=(((0.,0.,0.),rot,(scale,scale,1.)),)
    monkeypatch.setattr('utils.drivers.native_ui_layout.NativeUiLayout',Mock(return_value=native))
    if fault:
        with pytest.raises(ValueError):r.geometry((1366,768))
    else:
        v=r.geometry((1366,768));assert v.verified and v.canvas_size==(1920.,1080.)
        assert v.pan==(100.,200.) and v.scale==pytest.approx(1.19)


def marker_reader(monkeypatch):
    r=MapPanelReader.__new__(MapPanelReader);m=r.meta=Mock();r.memory=Mock();r.clock=lambda:20.
    r.snapshot=Mock(return_value=dict(state='projection_unverified',other_region_view=False,
        scene_context='s',view_identity='v',map_ratio=5.))
    r.owner=Mock(return_value=(1,2,3,300,301))
    r.find_class=Mock(return_value=41);r.static_instance=Mock(return_value=42)
    m.list_refs.return_value=[]
    r.expected=Mock();r.vector=Mock()
    refs={(42,'_currentMapSpot'):43,(300,'_spotWidgets'):400,(400,'head'):500,
          (500,'list'):400,(500,'item'):600,(500,'next'):500,(500,'prev'):500,
          (600,'<selfData>k__BackingField'):700,(600,'ui'):800,(800,'root_t'):900}
    m.ref.side_effect=lambda obj,name:refs[obj,name]
    payloads={(400,'count'):struct.pack('<i',1),(400,'version'):struct.pack('<i',2),
              (700,'category'):struct.pack('<i',7),(700,'usageId'):struct.pack('<I',25047276)}
    r.payload=Mock(side_effect=lambda obj,name,code,n:payloads[obj,name])
    layout=Mock();layout.rect.return_value={'anchored_position':(-1056.78894,-498.9049)}
    monkeypatch.setattr('utils.drivers.native_ui_layout.NativeUiLayout',Mock(return_value=layout))
    return r,m,refs,payloads,layout


def test_custom_markers_come_from_live_owned_widgets_not_a_stale_initial_map_list(monkeypatch):
    r,m,_,_,_=marker_reader(monkeypatch)
    result=r.custom_markers()
    assert result['25047276']==pytest.approx((-211.357788,0.,-99.78098))
    assert not any(c.args[1]=='_currentMapSpot' for c in m.ref.call_args_list)


@pytest.mark.parametrize('fault',['loop','count','version','widget_replaced','scene','ratio'])
def test_mutating_or_invalid_marker_graph_is_never_accepted(monkeypatch,fault):
    r,m,refs,p,layout=marker_reader(monkeypatch)
    if fault=='loop':refs[500,'next']=0
    elif fault=='count':p[400,'count']=struct.pack('<i',5000)
    elif fault=='version':
        original=r.payload.side_effect;reads=0
        def payload(obj,name,code,n):
            nonlocal reads
            if name=='version':
                reads+=1
                return struct.pack('<i',reads)
            return original(obj,name,code,n)
        r.payload.side_effect=payload
    elif fault=='widget_replaced':
        layout.rect.side_effect=lambda obj:refs.update({(600,'<selfData>k__BackingField'):701}) or {'anchored_position':(1.,2.)}
    elif fault=='scene':
        a=r.snapshot.return_value;r.snapshot.side_effect=[a,{**a,'scene_context':'other'}]
    else:r.snapshot.return_value['map_ratio']=0.
    with pytest.raises(ValueError):r.custom_markers()
