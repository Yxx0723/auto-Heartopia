from datetime import datetime,timedelta
from utils.drivers.special_resource_locations import SpecialResourceLocations
from utils.core.special_approach import select_special_target
from tests.unit.test_special_collection import follower
import pytest


def test_last_seen_position_is_only_a_search_hint_and_is_invalidated_on_day_or_session_change(tmp_path):
 now=[datetime.fromisoformat('2026-10-20T21:00:00+08:00')]
 store=SpecialResourceLocations(tmp_path/'locations.json',wall_clock=lambda:now[0])
 target={'category':'roaming_oak','position':[10,20,30],'resource_static_id':42001,
         'resource_global_validated':True,'resource_global_only':True,'map_dynamic':True}
 scene={'state':'ready','global_special_state':'ready','global_special_context':[1,2,3],
        'global_special_resources':[target],'objects':[]}
 assert not store.enrich(scene)['special_search_hints']
 scene['global_special_resources']=[]
 restored=SpecialResourceLocations(store.path,wall_clock=lambda:now[0]).enrich(scene)
 hint=select_special_target(restored,'roaming_oak','溜溜橡木')
 assert hint['resource_search_hint'] and 'resource_global_validated' not in hint and 'resource_available' not in hint
 assert not store.enrich({**scene,'global_special_context':[4,2,3]})['special_search_hints']
 store.enrich({**scene,'global_special_resources':[target]})
 now[0]+=timedelta(days=1)
 assert not store.enrich(scene)['special_search_hints']


def test_position_hint_never_authorizes_f_and_cooldown_is_not_overridden():
 f,game,point,target,nav=follower()
 hint={k:v for k,v in target.items() if k!='resource_available'}
 hint.update(resource_search_hint=True,resource_global_only=True)
 scene={'state':'ready','global_special_state':'ready','objects':[],'special_search_hints':[hint]}
 nav.scene_provider.return_value=scene
 assert f.special_target(point,allow_global=True) is hint
 with pytest.raises(ValueError):f.collect_point(point,0,1)
 assert not game.events
 scene['objects']=[{**target,'resource_available':0}]
 with pytest.raises(ValueError):select_special_target(scene,'fluorite','无暇萤石')


def test_corrupt_location_file_does_not_stop_scene_updates(tmp_path):
 path=tmp_path/'cache.json';path.write_text('broken')
 store=SpecialResourceLocations(path)
 scene={'state':'ready','global_special_state':'unavailable'}
 assert store.enrich(scene) is scene


def test_empty_old_anchor_is_rejected_and_not_renewed_by_backend_records(tmp_path):
 now=[0.]
 store=SpecialResourceLocations(tmp_path/'locations.json',clock=lambda:now[0])
 target={'category':'roaming_oak','position':[10,20,30],'resource_static_id':42001,
         'resource_global_validated':True,'resource_global_only':True,'map_dynamic':True}
 scene={'state':'ready','global_special_state':'ready','global_special_context':[1,2,3],
        'global_special_resources':[target],'objects':[],'position':[10,20,30]}
 store.enrich(scene);now[0]=16
 result=store.enrich(scene)
 assert not result['global_special_resources'] and not result['special_search_hints']
 with pytest.raises(ValueError,match='停用旧坐标'):select_special_target(result,'roaming_oak','溜溜橡木')
 restored=SpecialResourceLocations(store.path).enrich(scene)
 assert not restored['global_special_resources']
 assert 'observed_at' not in store.data['targets'].get('roaming_oak',{})


def test_live_cooldown_object_can_be_recognized_but_not_collected(tmp_path):
 now=[0.]
 store=SpecialResourceLocations(tmp_path/'locations.json',clock=lambda:now[0])
 target={'category':'roaming_oak','position':[10,20,30],'resource_static_id':42001,
         'resource_global_validated':True,'resource_global_only':True,'map_dynamic':True}
 scene={'state':'ready','global_special_state':'ready','global_special_context':[1,2,3],
        'global_special_resources':[target],'objects':[],'position':[10,20,30]}
 store.enrich(scene);now[0]=16;store.enrich(scene)
 live={**target,'resource_global_only':False,'resource_available':0}
 result=store.enrich({**scene,'objects':[live]})
 assert result['objects'][0]['resource_available']==0 and not result['special_resource_status']
 with pytest.raises(ValueError,match='采集标志不可用'):select_special_target(result,'roaming_oak','溜溜橡木')


def test_new_backend_position_is_not_permanently_blocked(tmp_path):
 now=[0.];store=SpecialResourceLocations(tmp_path/'locations.json',clock=lambda:now[0])
 target={'category':'roaming_oak','position':[10,20,30],'resource_static_id':42001,
         'resource_global_validated':True,'resource_global_only':True,'map_dynamic':True}
 scene={'state':'ready','global_special_state':'ready','global_special_context':[1,2,3],
        'global_special_resources':[target],'objects':[],'position':[10,20,30]}
 store.enrich(scene);now[0]=16;store.enrich(scene)
 new={**target,'position':[100,20,30]}
 result=store.enrich({**scene,'global_special_resources':[new]})
 assert result['global_special_resources']==[new] and not result['special_resource_status']
