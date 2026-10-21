"""Only synchronized live identities become map resources, without implying availability."""
import struct
import pytest
from utils.drivers.global_map_resources import decode_resource, GlobalMapResourceReader
from utils.ui.monitor_style import SCENE_CATEGORIES
from utils.ui.native_coordinate_monitor import CoordinateWindow
from utils.core.map_resources import combine_resource_markers
from unittest.mock import Mock


def values(kind='ServerInsectComponent',static=51000):
    return {'LocalPositionComponent':struct.pack('<3f',200,20,-100),kind:struct.pack('<i',static)+bytes(12)}


@pytest.mark.parametrize('kind,category',[('ServerInsectComponent','insect'),('ServerBirdComponent','bird')])
def test_creature_identity_and_position_are_current_but_interaction_unknown(kind,category):
    result=decode_resource(values(kind),123,2)
    assert result['category']==category and result['position']==[200,20,-100]
    assert result['interaction_state']=='unknown' and result['resource_global_validated']
    assert 'resource_available' not in result and not result.get('map_static')


@pytest.mark.parametrize('hidden,shadow_id,expected',[(0,10001,True),(1,10001,False),(0,10002,False)])
def test_fish_requires_visible_matching_shadow(hidden,shadow_id,expected):
    data=values('ServerFishComponent',10001)
    data['FishShadowComponent']=struct.pack('<iB3x',shadow_id,hidden)
    assert bool(decode_resource(data,1,2))==expected


def test_missing_fish_shadow_or_ambiguous_creature_does_not_guess():
    assert decode_resource(values('ServerFishComponent',10001),1,2) is None
    data=values()
    data.update(values('ServerBirdComponent'))
    assert decode_resource(data,1,2) is None


@pytest.mark.parametrize('item,category',[(102,'blueberry'),(103,'raspberry'),(207,'tree'),(301,'rock'),(1031,'resource')])
def test_map_resources_preserve_unknown_kinds_without_inventing_fruit_names(item,category):
    data={'LocalPositionComponent':struct.pack('<3f',0,20,0),
          'MapResourcePointComponent':struct.pack('<Iii',456,item,0)}
    result=decode_resource(data,1,2)
    assert result['category']==category and result['interaction_state']=='unknown'


def test_optional_pools_are_not_required_on_each_entity():
    reader=GlobalMapResourceReader.__new__(GlobalMapResourceReader)
    bindings={name:(i,None,None) for i,name in enumerate(('LocalPositionComponent','ServerBirdComponent','ServerFishComponent','MapResourcePointComponent'))}
    assert reader.select_components({0:4,1:3},bindings)==['LocalPositionComponent','ServerBirdComponent']
    assert not reader.select_components({1:3},bindings)
    assert not reader.select_components({0:4},bindings)


@pytest.mark.parametrize('category',['fish','bird','insect','crop','resource','blueberry'])
def test_new_categories_have_symbols_and_merge_as_full_map_references(category):
    assert category in SCENE_CATEGORIES
    canvas=Mock()
    CoordinateWindow.draw_scene_symbol(canvas,2,3,category)
    assert canvas.method_calls
    marker={'category':category,'position':[300,20,0],'resource_global_validated':True}
    assert combine_resource_markers([], [marker])[0]['map_global']

@pytest.mark.parametrize('name,namespace,expected',[
    ('FishComponent','XDTLevelAndEntity.Gameplay.Component.Fish','fish'),
    ('CropComponent','XDTLevelAndEntity.Gameplay.Component.Homeland','crop'),
    ('CollectableObjectComponent','XDTLevelAndEntity.Gameplay.Component.Gather','resource'),
    ('AquariumFishComponent','XDTLevelAndEntity.Gameplay.Component.Fish','other'),
    ('CropComponent','Unrelated.Homeland','other')])
def test_harvest_and_fish_require_exact_owned_components(name,namespace,expected):
    from tests.unit.test_mono_scene import shrub_runtime
    r,scene,owner,entity,_,components=shrub_runtime()
    r.add_class(name,namespace,[],parent='GatherComponent',size=48)
    component=r.make_object(name,48)
    r.p64(component+16,entity)
    entries=struct.unpack('<Q',r.read(components+24,8))[0]
    r.p64(entries+48,component)
    assert scene.object_category(owner,scene.meta.obj(owner))==expected
    r.p64(component+16,r.entity)
    assert scene.object_category(owner,scene.meta.obj(owner))=='other'
