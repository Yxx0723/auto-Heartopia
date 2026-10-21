"""Global resources are display references, never permission to send F."""
import struct
from unittest.mock import Mock
import pytest
from utils.drivers.global_special_resources import decode_marker
from utils.core.map_resources import combine_resource_markers
from utils.core.special_approach import select_special_target
from utils.ui.map_draw_cache import MapDrawCache
from utils.ui.map_draw_cache import Viewport


def marker(static=42001, item=401, xyz=(176.19,22.873,-15.954), resource=0x800003e9, dynamic=1001, generation=2):
    return decode_marker(struct.pack('<Iii',resource,item,static),struct.pack('<i',dynamic),
                         struct.pack('<3f',*xyz),6239,generation)


@pytest.mark.parametrize('static,item,category',[(42001,401,'roaming_oak'),(42002,402,'fluorite')])
def test_typed_dynamic_identity_has_no_fabricated_availability(static,item,category):
    result=marker(static,item)
    assert result['category']==category
    assert result['resource_global_only'] and result['resource_global_validated']
    assert 'resource_available' not in result
    assert 'map_static' not in result and 'map_definition' not in result


@pytest.mark.parametrize('static,item',[(42001,402),(42002,401),(100,401),(42003,403)])
def test_other_resources_and_crossed_ids_are_not_promoted(static,item):
    assert marker(static,item) is None


@pytest.mark.parametrize('kwargs',[{'dynamic':1002},{'dynamic':0},{'generation':0},
    {'xyz':(float('nan'),0,0)},{'xyz':(0,float('inf'),0)}])
def test_invalid_identity_or_coordinates_are_rejected(kwargs):
    with pytest.raises(ValueError): marker(**kwargs)


def test_far_markers_bypass_nearby_radius_and_disappear_when_world_data_removed():
    cache=MapDrawCache(Mock())
    view=Viewport(1000,600,0,0,1)
    scene={'objects':[],'colliders':[],'global_special_resources':[marker(xyz=(300,20,0))]}
    objects,_=cache.geometry.select(scene,[0,20,0],80,view)
    assert len(objects)==1 and objects[0]['category']=='roaming_oak'
    scene['global_special_resources']=[]
    assert not cache.geometry.select(scene,[0,20,0],80,view)[0]


def test_live_interaction_state_wins_over_global_navigation_reference():
    reference=marker()
    live={**reference,'resource_global_only':False,'resource_available':2}
    live.pop('map_global')
    result=combine_resource_markers([live],[reference])
    assert len(result)==1 and result[0]['resource_available']==2
    assert not result[0]['resource_global_only']
    assert 'map_global' not in live


def test_global_reference_alone_cannot_start_f_collection():
    with pytest.raises(ValueError):
        select_special_target({'state':'ready','objects':[], 'global_special_resources':[marker()]},
                              'roaming_oak','溜溜橡木')


@pytest.mark.parametrize('flags,count,generation,world,expected',[(0x1003b,3,2,0,1),(0,-1,3,0,0),(0,3,0,0,0),(0,3,2,1,0)])
def test_backend_entity_liveness_uses_generation_and_components_not_zero_flags(monkeypatch,flags,count,generation,world,expected):
    from utils.drivers.global_special_resources import GlobalSpecialReader
    from utils.drivers import stamina
    from types import SimpleNamespace
    raw=bytearray(96)
    struct.pack_into('<3H',raw,0,11,12,13)
    struct.pack_into('<3i',raw,16,0,0,0)
    struct.pack_into('<iiQ',raw,48,count,0,0)
    struct.pack_into('<II',raw,64,generation,flags)
    struct.pack_into('<I',raw,88,world)
    memory={10000:bytes(raw),20000:struct.pack('<Iii',0x800003e9,401,42001),
            30000:struct.pack('<i',1001),40000:struct.pack('<3f',176,22,-15)}
    references={(1,'_pool'):101,(2,'_pool'):102,(3,'_pool'):103,
                (101,'_items'):201,(102,'_items'):202,(103,'_items'):203}
    pointers={508:600,608:700,700:800}
    def field(info,name,*unused):
        if name=='_entities':return {'offset':96,'type':500}
        return {'offset':32}
    reader=GlobalSpecialReader.__new__(GlobalSpecialReader)
    context=(0,0,0,999,0,0)
    reader.context=lambda:context
    reader.pools=lambda _: {'MapResourcePointComponent':(11,1,{'size':28}),
                            'DynamicMapEntityComponent':(12,2,{'size':20}),
                            'LocalPositionComponent':(13,3,{'size':28})}
    reader.expected=lambda obj,*_:obj
    reader.field=field
    reader.meta=SimpleNamespace(ref=lambda obj,name:references[obj,name],obj=lambda obj:obj,
        pointer=lambda at:pointers[at],cls=lambda _: {'name':'EcsEntityData','namespace':'','size':112},
        data=lambda at,size:memory[at][:size])
    class Store:
        def __init__(self,read,address,stride):
            self.pointer={1095:10000,233:20000,234:30000,235:40000}[address]
            self.count=1
        def at(self,index):assert index==0;return self.pointer
        def stable(self):return True
    monkeypatch.setattr(stamina,'NativeGrowList',Store)
    assert len(reader.snapshot())==expected
