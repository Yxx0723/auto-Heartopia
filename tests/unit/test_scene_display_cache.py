"""Display history is bounded, world-scoped, and cannot enter navigation."""
from unittest.mock import Mock

import pytest

from utils.core import thresholds as policy
from utils.core.scene_display_cache import SceneDisplayCache
from utils.ui.scene_map_worker import SceneMapWorker


def plant(x, identity='tree', category='tree'):
    return {'position': [x, 0., 0.], 'category': category, 'map_static': True,
            'map_id': ('plant', identity)}


def rail(x, identity='rail'):
    return {'polygon': [[x, -1.], [x+.2, -1.], [x+.2, 1.], [x, 1.]], 'height': [0., 1.],
            'source': 'verified-solid-collider', 'category': 'fence',
            'map_static': True, 'map_id': ('world-box', identity)}


def scene(*objects, boxes=(), x=0., context=('world', 1)):
    return {'state': 'ready', 'world_state': 'ready', 'cache_context': context,
            'position': [x, 0., 0.], 'objects': list(objects), 'colliders': list(boxes)}


def test_walked_fixed_landmarks_are_retained_far_away_but_not_dynamic_entities():
    cache = SceneDisplayCache()
    bush, fence, animal = plant(0, category='blueberry'), rail(1), plant(2, category='animal')
    original = scene(bush, animal, boxes=[fence])
    first = cache.update(original, 0.)
    assert len(first['objects']) == 2 and len(first['colliders']) == 1
    second = cache.update(scene(x=200), 2.)
    assert second['objects'] == [{**bush, 'map_cached': True}]
    assert second['colliders'] == [{**fence, 'map_cached': True}]
    assert second['display_cache'] == {'fixed_items': 2, 'cached_items': 2, 'cells': 1}
    assert 'map_cached' not in bush and 'map_cached' not in fence and 'display_cache' not in original


def test_fresh_near_absence_removes_obsolete_fixed_landmarks():
    cache = SceneDisplayCache()
    cache.update(scene(plant(140), boxes=[rail(142)]), 0.)
    assert cache.update(scene(x=300), 1.)['display_cache']['cached_items'] == 2
    display = cache.update(scene(x=140), 2.)
    assert display['objects'] == display['colliders'] == [] and cache.count == 0


def test_far_fixed_geometry_refreshes_less_often_and_near_updates_are_immediate():
    cache = SceneDisplayCache()
    cache.update(scene(plant(140)), 0.)
    assert cache.update(scene(plant(141)), 2.)['objects'][0]['position'][0] == 140
    assert cache.update(scene(plant(142)), policy.COORD_SCENE_CACHE_FAR_SECONDS)['objects'][0]['position'][0] == 142
    display = cache.update(scene(plant(143), x=140), policy.COORD_SCENE_CACHE_FAR_SECONDS+1)
    assert display['objects'][0]['position'][0] == 143 and not display['objects'][0].get('map_cached')


@pytest.mark.parametrize('failure', ['context', 'error', 'native_reload', 'invalid_position'])
def test_world_changes_or_invalid_snapshots_drop_history(failure):
    cache = SceneDisplayCache()
    cache.update(scene(plant(140)), 0.)
    fresh = scene()
    if failure == 'context': fresh['cache_context'] = ('world', 2)
    if failure == 'error': fresh['state'] = 'error'
    if failure == 'native_reload': fresh['world_state'] = 'unavailable'
    if failure == 'invalid_position': fresh['position'] = [float('nan'), 0, 0]
    display = cache.update(fresh, 1.)
    assert display['objects'] == [] and cache.count == 0


def test_history_expires_and_current_observations_survive_capacity_limits():
    cache = SceneDisplayCache(max_items=2, max_cells=2)
    landmarks = [plant(140, 'a'), plant(240, 'b'), plant(340, 'c')]
    display = cache.update(scene(*landmarks), 0.)
    assert len(display['objects']) == 3 and cache.count == 2 and len(cache.cells) == 2
    retained = cache.update(scene(), 1.)
    assert {o['map_id'] for o in retained['objects']} == {('plant', 'b'), ('plant', 'c')}
    expired = cache.update(scene(), policy.COORD_SCENE_CACHE_TTL+2)
    assert expired['objects'] == [] and cache.count == 0


def test_whole_chunk_eviction_is_bounded_even_when_a_dense_chunk_exceeds_capacity():
    cache = SceneDisplayCache(max_items=2)
    display = cache.update(scene(plant(140, 'a'), plant(141, 'b'), plant(142, 'c')), 0.)
    assert len(display['objects']) == 3 and cache.count <= 2


def test_unidentified_and_home_built_obstacles_are_not_retained():
    cache = SceneDisplayCache()
    unknown = {'position': [140, 0, 0], 'category': 'other', 'map_static': True, 'map_id': 'unknown'}
    home = {k: v for k, v in rail(141).items() if k not in ('map_static', 'map_id')}
    cache.update(scene(unknown, boxes=[home]), 0.)
    display = cache.update(scene(), 2.)
    assert display['objects'] == display['colliders'] == []


def test_json_round_trip_context_and_ids_do_not_break_cache_identity():
    import json
    cache = SceneDisplayCache()
    cache.update(scene(plant(140)), 0.)
    display = cache.update(json.loads(json.dumps(scene(plant(140)))), 2.)
    assert len(display['objects']) == cache.count == 1


def test_cached_rail_is_visible_but_navigation_receives_only_the_live_snapshot():
    now = [0.]
    reader = Mock()
    reader.snapshot.return_value = scene(boxes=[rail(0)])
    worker = SceneMapWorker('profile', factory=lambda *a: reader, clock=lambda: now[0])
    worker.set_pid(77)
    worker.poll_once()
    fresh = scene(x=200)
    reader.snapshot.return_value = fresh
    now[0] = 2.
    worker.poll_once()
    display = worker.take_latest()
    assert display['colliders'][0]['map_cached']
    assert worker.navigation_snapshot(77) is fresh and fresh['colliders'] == []
    worker.clear_history()
    worker.poll_once()
    assert worker.take_latest()['colliders'] == []
    worker.close()


def test_clear_during_a_read_cannot_republish_old_history():
    reader = Mock()
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(77)
    reader.snapshot.return_value = scene(plant(140))
    worker.poll_once()
    def sample():
        worker.clear_history()
        return scene()
    reader.snapshot.side_effect = sample
    worker.poll_once()
    assert worker.take_latest()['objects'] == [] and worker.history.count == 0
    worker.close()


def test_new_process_cannot_reuse_previous_process_landmarks():
    reader = Mock()
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(77)
    reader.snapshot.return_value = scene(plant(140))
    worker.poll_once()
    worker.set_pid(88)
    reader.snapshot.return_value = scene()
    worker.poll_once()
    assert worker.take_latest()['objects'] == []
    worker.close()
