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
