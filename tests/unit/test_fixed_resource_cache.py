"""Fixed definitions persist; creature positions remain exclusively in live snapshots."""
from unittest.mock import Mock
import json
import pytest
from utils.drivers.fixed_resource_cache import FixedResourceCache

CTX={'map':'main','build':'a'*64}


def point(identity=1,category='tree',x=500):
 return {'resource_id':identity,'category':category,'position':[x,20,30],
         'map_definition':True,'item_type_id':207}


def scene(points=None,**extra):
 return {'state':'ready','fixed_resource_context':CTX,'global_resource_state':'ready',
         'global_resources':[point()] if points is None else points,'objects':[],**extra}


def test_static_cache_survives_restart_without_persisting_creatures(tmp_path):
 path=tmp_path/'fixed.json';cache=FixedResourceCache(path)
 fish={'category':'fish','position':[1,2,3],'map_dynamic':True}
 data=scene([point(),point(2,'blueberry',600),point(3,'fish',700)],objects=[fish])
 result=cache.enrich(data)
 assert len(result['global_resources'])==2
 assert result['objects'] is data['objects'] and 'source' not in data['global_resources'][0]
 restored=FixedResourceCache(path).enrich(scene([],global_resource_state='unavailable',objects=[]))
 assert len(restored['global_resources'])==2
 assert all(o.get('map_cached') for o in restored['global_resources'])
 assert restored['objects']==[]
 assert 'fish' not in path.read_text()


def test_foreign_map_or_game_build_does_not_show_old_points(tmp_path):
 cache=FixedResourceCache(tmp_path/'fixed.json');cache.enrich(scene())
 for context in (None,{'map':'other','build':'a'*64},{'map':'main','build':'b'*64}):
  current=scene([],fixed_resource_context=context,global_resource_state='unavailable')
  assert not cache.enrich(current)['global_resources']


def test_fresh_definitions_replace_old_configuration_without_losing_live_data(tmp_path):
 cache=FixedResourceCache(tmp_path/'fixed.json');cache.enrich(scene())
 fish={'category':'fish','position':[9,2,3]}
 current=scene([point(2,'ore',800)],objects=[fish])
 result=cache.enrich(current)
 assert [p['resource_id'] for p in result['global_resources']]==[2]
 assert result['objects'] is current['objects'] and current['global_resources'][0].get('source') is None


def test_unchanged_data_does_not_rewrite_cache(tmp_path,monkeypatch):
 from utils.drivers import fixed_resource_cache
 cache=FixedResourceCache(tmp_path/'fixed.json')
 writer=Mock(wraps=fixed_resource_cache.atomic_write_json)
 monkeypatch.setattr(fixed_resource_cache,'atomic_write_json',writer)
 cache.enrich(scene());cache.enrich(scene());cache.enrich(scene())
 assert writer.call_count==1


def test_corrupt_cache_falls_back_to_live_definitions(tmp_path):
 path=tmp_path/'fixed.json';path.write_text('broken')
 result=FixedResourceCache(path).enrich(scene())
 assert len(result['global_resources'])==1
 assert json.loads(path.read_text())['schema']=='fixed-resources-v1'


def test_cache_write_failure_does_not_stop_live_map(tmp_path,monkeypatch):
 from utils.drivers import fixed_resource_cache
 monkeypatch.setattr(fixed_resource_cache,'atomic_write_json',Mock(side_effect=OSError('disk full')))
 current=scene();result=FixedResourceCache(tmp_path/'fixed.json').enrich(current)
 assert result['state']=='ready' and len(result['global_resources'])==1


def test_worker_uses_static_cache_only_for_display_and_keeps_fauna_live(tmp_path):
 from utils.ui.scene_map_worker import SceneMapWorker
 fish1={'category':'fish','position':[1,20,3]}
 fish2={'category':'fish','position':[9,20,3]}
 base={'world_state':'ready','cache_context':[77,'main'],'position':[0,20,0],'colliders':[]}
 reader=Mock()
 reader.snapshot.side_effect=[scene(objects=[fish1],**base),
   scene([],global_resource_state='unavailable',objects=[fish2],**base),
   scene([],global_resource_state='unavailable',objects=[],**base)]
 worker=SceneMapWorker('profile',factory=lambda *a:reader,clock=lambda:100,
                       fixed_resources_path=tmp_path/'fixed.json')
 worker.set_pid(77)
 worker.poll_once();worker.take_latest()
 worker.poll_once();display=worker.take_latest()
 assert len(display['global_resources'])==1 and display['objects']==[fish2]
 assert worker.navigation_snapshot(77)['global_resources']==[]
 worker.poll_once();assert worker.take_latest()['objects']==[]
 worker.close()
