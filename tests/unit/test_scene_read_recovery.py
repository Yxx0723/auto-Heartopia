"""Structured scene failures, bounded recovery and navigation isolation."""
import struct
from unittest.mock import Mock
import pytest
from tests.unit.test_mono_scene import dictionary
from utils.drivers.mono_scene import SceneMetadata
from utils.ui.scene_map_worker import SceneMapWorker


def test_mutation_has_structured_cause_and_stage_without_weakening_checks():
    r, obj = dictionary()
    entries = struct.unpack('<Q', r.read(obj+24, 8))[0]
    def read(at, size):
        data = r.read(at, size)
        if at == entries+32:
            r.write(obj+76, struct.pack('<i', 1))
        return data
    meta = SceneMetadata(read, r.domain)
    meta.stage = 'registry_end'
    with pytest.raises(ValueError) as caught:
        meta.dictionary(obj)
    issue = caught.value
    assert issue.kind == 'mutation'
    assert issue.stage == 'registry_end'
    assert issue.retryable and not issue.scope_verified


def test_invalid_reference_is_classified_but_never_declared_retryable():
    r, obj = dictionary()
    r.p64(obj+24, 1)
    meta = SceneMetadata(r.read, r.domain)
    meta.stage = 'entities'
    with pytest.raises(ValueError) as caught:
        meta.pointer(obj+24)
    assert caught.value.kind == 'invalid_reference'
    assert caught.value.stage == 'entities'
    assert not caught.value.retryable


def test_unknown_error_keeps_legacy_close_and_safe_diagnostics():
    reader = Mock()
    reader.snapshot.side_effect = ValueError('bad pointer 0x1234')
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(7)
    worker.poll_once()
    result = worker.take_latest()
    assert result['issue'] == {'kind': 'unknown', 'stage': 'snapshot', 'retryable': False,
                               'scope_verified': False}
    assert '0x' not in result['message'] and '0x' not in str(result['issue'])
    reader.close.assert_called_once()


@pytest.mark.parametrize('fault', ['mutation', 'unknown', 'invalid_reference', 'scope_changed'])
def test_local_retry_is_exclusively_for_verified_mutation(fault):
    from utils.core.scene_read import RecoveryBudget, SceneReadIssue, read_collection
    issue = SceneReadIssue('failure', kind=fault, retryable=fault == 'mutation')
    read = Mock(side_effect=[issue, [123]])
    resolve, guard = Mock(return_value=77), Mock()
    budget = RecoveryBudget()
    if fault == 'mutation':
        assert read_collection(resolve, read, guard, budget, stage='registry_end') == [123]
        assert read.call_count == 2 and resolve.call_count == 2 and budget.retries == 1
    else:
        with pytest.raises(SceneReadIssue):
            read_collection(resolve, read, guard, budget, stage='registry_end')
        assert read.call_count == 1 and budget.retries == 0


def test_continuous_mutation_stops_after_two_total_attempts():
    from utils.core.scene_read import RecoveryBudget, SceneReadIssue, read_collection
    read = Mock(side_effect=lambda *a: (_ for _ in ()).throw(
        SceneReadIssue('changing', kind='mutation', retryable=True)))
    with pytest.raises(SceneReadIssue) as caught:
        read_collection(lambda: 77, read, lambda: None, RecoveryBudget(), stage='registry_end')
    assert read.call_count == 2
    assert caught.value.scope_verified and caught.value.stage == 'registry_end'


def test_scope_change_during_mutation_never_retries():
    from utils.core.scene_read import RecoveryBudget, SceneReadIssue, read_collection
    read = Mock(side_effect=SceneReadIssue('changing', kind='mutation', retryable=True))
    guard = Mock(side_effect=[None, SceneReadIssue('world changed', kind='scope_changed')])
    with pytest.raises(SceneReadIssue, match='world changed'):
        read_collection(lambda: 77, read, guard, RecoveryBudget(), stage='registry_start')
    assert read.call_count == 1


def test_retry_budget_is_shared_and_does_not_restart_for_each_collection():
    from utils.core.scene_read import RecoveryBudget, SceneReadIssue, read_collection
    budget = RecoveryBudget(max_retries=1)
    def changing_then_stable():
        return Mock(side_effect=[SceneReadIssue('changing', kind='mutation', retryable=True), [1]])
    assert read_collection(lambda: 77, changing_then_stable(), lambda: None, budget, stage='first') == [1]
    read = changing_then_stable()
    with pytest.raises(SceneReadIssue):
        read_collection(lambda: 77, read, lambda: None, budget, stage='second')
    assert read.call_count == 1


def test_expensive_retry_uses_up_extra_time_budget():
    from utils.core.scene_read import RecoveryBudget, SceneReadIssue, read_collection
    now = [0.]
    budget = RecoveryBudget(clock=lambda: now[0], max_seconds=.1)
    calls = [0]
    def read(at):
        calls[0] += 1
        if calls[0] == 1: raise SceneReadIssue('changing', kind='mutation', retryable=True)
        now[0] += .2
        return [1]
    assert read_collection(lambda: 77, read, lambda: None, budget, stage='first') == [1]
    assert budget.spent == pytest.approx(.2)
    again = Mock(side_effect=SceneReadIssue('changing', kind='mutation', retryable=True))
    with pytest.raises(SceneReadIssue):
        read_collection(lambda: 77, again, lambda: None, budget, stage='second')
    assert again.call_count == 1


def test_cancel_during_first_read_prevents_retries_and_publication():
    from utils.core.scene_read import RecoveryBudget, SceneReadIssue, read_collection
    stopped = [False]
    def read(at):
        stopped[0] = True
        return [1]
    with pytest.raises(SceneReadIssue) as caught:
        read_collection(lambda: 77, read, lambda: None,
                        RecoveryBudget(cancelled=lambda: stopped[0]), stage='first')
    assert caught.value.kind == 'cancelled'



def test_real_registry_decoder_recovers_one_mutation_with_fresh_stamp():
    from utils.core.scene_read import RecoveryBudget
    from utils.drivers.mono_scene import MemorySceneMap
    r, obj = dictionary()
    entries = struct.unpack('<Q', r.read(obj+24, 8))[0]
    changed = [False]
    def read(at, size):
        data = r.read(at, size)
        if at == entries+32 and not changed[0]:
            changed[0] = True
            r.write(obj+76, struct.pack('<i', 1))
        return data
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = SceneMetadata(read, r.domain)
    reader.meta.stage = 'registry_start'
    reader.meta.static_ref = lambda *a: obj
    reader.memory = Mock()
    reader.memory.snapshot.return_value = {'paths': [{'trace': [{'field': '_hierarchy', 'pointer': r.hierarchy}]}]}
    budget = RecoveryBudget()
    assert reader.read_registry(obj, 99, r.hierarchy, budget) == [r.hierarchy]
    assert budget.retries == 1


def test_prechecked_empty_active_registry_is_not_read_a_third_time():
    from utils.drivers.mono_scene import MemorySceneMap
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = Mock()
    assert reader.refresh_live_objects([(1, 11, {'category': 'insect'})], 77, Mock(), active=[]) == []
    reader.meta.dictionary.assert_not_called()


def test_root_change_in_registry_guard_rejects_even_successful_collection():
    from utils.core.scene_read import RecoveryBudget, SceneReadIssue
    from utils.drivers.mono_scene import MemorySceneMap
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = Mock(stage='registry_end')
    reader.meta.static_ref.side_effect = [77, 77, 88]
    reader.meta.dictionary.return_value = [1]
    reader.memory = Mock()
    reader.memory.snapshot.return_value = {'paths': [{'trace': [{'field': '_hierarchy', 'pointer': 1}]}]}
    with pytest.raises(SceneReadIssue) as caught:
        reader.read_registry(77, 99, 1, RecoveryBudget())
    assert caught.value.kind == 'scope_changed' and not caught.value.scope_verified



def test_verified_transient_failure_retains_reader_but_invalidates_navigation():
    from utils.core.scene_read import SceneReadIssue
    reader = Mock()
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(7)
    reader.snapshot.return_value = {'state': 'ready', 'world_state': 'ready', 'objects': [], 'colliders': []}
    worker.poll_once()
    assert worker.navigation_snapshot(7)
    reader.snapshot.side_effect = SceneReadIssue('changing', kind='mutation', stage='registry_end',
                                                retryable=True, scope_verified=True)
    worker.poll_once()
    reader.close.assert_not_called()
    assert worker.navigation_snapshot(7) is None
    assert worker.retry_delay == pytest.approx(.1)
    assert worker.take_latest()['objects'] == []  # No unapproved old-icon retention.
    reader.snapshot.side_effect = None
    worker.poll_once()
    assert worker.navigation_snapshot(7) and worker.transient_failures == 0


@pytest.mark.parametrize('verified', [False, True])
def test_unverified_or_scope_changed_failures_keep_close_protection(verified):
    from utils.core.scene_read import SceneReadIssue
    reader = Mock()
    reader.snapshot.side_effect = SceneReadIssue('changed', kind='scope_changed' if verified else 'mutation',
                                                retryable=True, scope_verified=verified)
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(7)
    worker.poll_once()
    reader.close.assert_called_once()
    assert worker.navigation_snapshot(7) is None


def quality_scene(observed, *, eligible=True, managed='complete', native='complete'):
    return {'state': 'ready', 'world_state': 'ready', 'objects': [], 'colliders': [],
            'quality': {'navigation_eligible': eligible, 'root_verified_at': observed,
                        'parts': {'entities': {'state': 'complete'},
                                  'managed_collisions': {'state': managed, 'observed_from': observed, 'observed_to': observed},
                                  'native_collisions': {'state': native, 'observed_from': observed, 'observed_to': observed}}}}


@pytest.mark.parametrize('fault', ['partial', 'ineligible', 'old', 'future', 'missing_part', 'nan'])
def test_navigation_quality_gate_rejects_partial_stale_or_malformed_snapshots(fault):
    now = [10.]
    scene = quality_scene(10.)
    if fault == 'partial': scene['quality']['parts']['managed_collisions']['state'] = 'partial'
    elif fault == 'ineligible': scene['quality']['navigation_eligible'] = False
    elif fault == 'old': scene = quality_scene(0.)
    elif fault == 'future': scene = quality_scene(11.)
    elif fault == 'missing_part': del scene['quality']['parts']['native_collisions']
    else: scene['quality']['parts']['native_collisions']['observed_from'] = float('nan')
    reader = Mock()
    reader.snapshot.return_value = scene
    worker = SceneMapWorker('profile', factory=lambda *a: reader, clock=lambda: now[0])
    worker.set_pid(7)
    worker.poll_once()
    assert worker.take_latest()['state'] == 'ready'
    assert worker.navigation_snapshot(7) is None


def test_republishing_old_geometry_never_refreshes_its_observation_time():
    now = [0.]
    reader = Mock()
    reader.snapshot.return_value = quality_scene(0.)
    worker = SceneMapWorker('profile', factory=lambda *a: reader, clock=lambda: now[0])
    worker.set_pid(7)
    worker.poll_once()
    assert worker.navigation_snapshot(7)
    now[0] = 7.
    worker.poll_once()
    assert worker.latest_at == 7.
    assert worker.navigation_snapshot(7) is None


def minimal_scene_reader():
    from utils.drivers.mono_scene import MemorySceneMap
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.memory = Mock(pid=7, base=1, profile={})
    reader.memory.snapshot.return_value = {'position': [1., 2., 3.], 'paths': [{'trace': [{'field': '_hierarchy', 'pointer': 1}]}]}
    reader.meta = Mock(domain=1)
    reader.meta.obj.return_value = {'address': 99}
    reader.meta.static_ref.return_value = 77
    reader.meta.dictionary.return_value = []
    reader.native = Mock(scene_key=('town',))
    reader.native.snapshot.return_value = ([], {})
    reader.native.main_terrain_loaded.return_value = False
    reader.global_minerals = Mock()
    reader.global_minerals.snapshot.return_value = []
    reader.house_entrances = Mock(cache={'context': 'town'})
    reader.house_entrances.snapshot.return_value = []
    return reader


def test_real_snapshot_produces_usable_quality_and_observation_bounds():
    from utils.core.scene_read import navigation_quality_valid
    import time
    reader = minimal_scene_reader()
    scene = reader.snapshot()
    assert scene['quality']['navigation_eligible']
    assert navigation_quality_valid(scene, time.monotonic())
    assert scene['quality']['parts']['managed_collisions']['state'] == 'complete'


def test_real_native_failure_still_publishes_entities_but_never_usable_navigation():
    reader = minimal_scene_reader()
    reader.native.snapshot.side_effect = ValueError('pool changed')
    scene = reader.snapshot()
    assert scene['state'] == 'ready' and scene['world_state'] == 'unavailable'
    assert not scene['quality']['navigation_eligible']


def test_worker_transient_backoff_is_bounded_and_does_not_leak_diagnostics(tmp_path):
    from utils.core.scene_read import SceneReadIssue
    reader = Mock()
    reader.snapshot.side_effect = SceneReadIssue('pointer 0xDEADBEEF', kind='mutation',
                                                stage='registry_start', retryable=True, scope_verified=True)
    path = tmp_path/'scene.jsonl'
    worker = SceneMapWorker('profile', factory=lambda *a: reader, diagnostics_path=path)
    worker.set_pid(7)
    for i in range(9): worker.poll_once()
    assert worker.retry_delay == 2. and worker.transient_failures == 9
    worker.close()
    text = path.read_text(encoding='utf-8')
    assert 'registry_start' in text and 'DEADBEEF' not in text



def test_unknown_owner_cannot_hide_a_missing_physics_group_from_quality():
    reader = minimal_scene_reader()
    reader.meta.dictionary.return_value = [2]
    reader.meta.ref.return_value = 22
    def obj(at):
        if at == 22: raise ValueError('unreadable owner')
        return {'address': 99}
    reader.meta.obj.side_effect = obj
    scene = reader.snapshot()
    assert scene['quality']['discovery_unknown'] == 1
    assert scene['quality']['parts']['managed_collisions']['state'] == 'partial'
    assert not scene['quality']['navigation_eligible']


def test_cancelled_real_snapshot_does_not_start_a_memory_scan():
    from utils.core.scene_read import SceneReadIssue
    reader = minimal_scene_reader()
    reader.cancelled = lambda: True
    with pytest.raises(SceneReadIssue) as caught:
        reader.snapshot()
    assert caught.value.kind == 'cancelled'
    reader.memory.snapshot.assert_not_called()


def test_partial_entities_are_not_used_as_confirmed_absence_or_old_icon_retention():
    from utils.core.scene_display_cache import SceneDisplayCache
    cache = SceneDisplayCache()
    tree = {'category': 'tree', 'map_static': True, 'map_id': ('plant', 1), 'position': [140, 0, 0]}
    base = {'state': 'ready', 'world_state': 'ready', 'cache_context': ('town', 1),
            'position': [0, 0, 0], 'objects': [tree], 'colliders': []}
    cache.update(base, 0.)
    assert cache.count == 1
    partial = {**base, 'objects': [], 'quality': {'parts': {'entities': {'state': 'partial'}}}}
    display = cache.update(partial, 1.)
    assert display['objects'] == [] and cache.count == 0


def test_late_transient_error_does_not_pollute_new_pid_backoff():
    from utils.core.scene_read import SceneReadIssue
    reader = Mock()
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(7)
    def snapshot():
        worker.set_pid(8)
        raise SceneReadIssue('changing', kind='mutation', retryable=True, scope_verified=True)
    reader.snapshot.side_effect = snapshot
    worker.poll_once()
    assert worker.transient_failures == 0 and worker.retry_delay == 2.
    assert worker.take_latest() is None and worker.navigation_snapshot(8) is None



@pytest.mark.parametrize('quality', [None, {}, {'parts': None}, {'parts': {'entities': None}}])
def test_malformed_quality_is_rejected_without_killing_worker_or_logging(tmp_path, quality):
    reader = Mock()
    reader.snapshot.return_value = {'state': 'ready', 'world_state': 'ready', 'objects': [],
                                   'colliders': [], 'quality': quality}
    worker = SceneMapWorker('profile', factory=lambda *a: reader, diagnostics_path=tmp_path/'scene.jsonl')
    worker.set_pid(7)
    worker.poll_once()
    assert worker.navigation_snapshot(7) is None
    assert worker.take_latest()['objects'] == []
    worker.close()
