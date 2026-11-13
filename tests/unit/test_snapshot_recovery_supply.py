"""Supply recovery never authorizes incomplete or previous collision data."""
from unittest.mock import Mock

import pytest

from utils.core.scene_read import RecoveryBudget, SceneReadIssue, read_linked_collection
from utils.drivers.mono_scene import NavigationSceneMap
from utils.ui.scene_map_worker import SceneMapWorker


@pytest.mark.parametrize('decision', ['rescan_verified_stream', 'existing_policy'])
def test_only_verified_removed_owner_gets_one_fresh_snapshot(decision):
    from utils.drivers.mono_scene import MemorySceneMap

    reader = MemorySceneMap.__new__(MemorySceneMap)
    attempts = []
    error = SceneReadIssue('invalid owner', kind='invalid_reference')

    def attempt():
        attempts.append(None)
        reader.collision_reference_failures = [{'recovery_decision': decision, 'owner': 'test-owner'}]
        reader.last_read_stats = {'attempt': len(attempts)}
        raise error

    reader._snapshot_attempt = attempt
    with pytest.raises(SceneReadIssue) as caught:
        reader.snapshot()
    assert caught.value is error
    assert len(attempts) == (2 if decision == 'rescan_verified_stream' else 1)


def test_scene_wait_intervals_count_elapsed_monotonic_time_once():
    from utils.flows.coordinate_player import _Follower

    follower = _Follower.__new__(_Follower)
    follower.clock = Mock()
    follower.clock.monotonic.return_value = 13.
    follower.scene_wait_interval = {'id': 1, 'started_at': 10., 'waypoint': 2}
    follower.scene_wait_total = follower.scene_wait_longest = 0.
    follower.finish_scene_wait('navigation_ready')
    follower.finish_scene_wait('stopped')
    assert follower.scene_wait_total == follower.scene_wait_longest == 3.
    assert follower.last_scene_wait['outcome'] == 'navigation_ready'


def test_native_partial_read_preserves_byte_count_and_error(monkeypatch):
    import ctypes
    from utils.drivers.coordinate_memory import ReadOnlyPosition

    memory = ReadOnlyPosition.__new__(ReadOnlyPosition)
    memory.handle = 1
    memory.kernel = Mock()
    def read(handle, address, buffer, size, count):
        count._obj.value = 3
        return False
    memory.kernel.ReadProcessMemory.side_effect = read
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 299)
    memory.reference_region = Mock(return_value={'memory_region_status': 'committed_readable'})
    observation = {}
    assert memory._read(0x10000, 8, observation=observation) is None
    assert observation == {'actual_bytes_read': 3, 'requested_bytes': 8,
                           'native_error_code': 299, 'memory_read_status': 'partial',
                           'memory_region_status': 'committed_readable'}


@pytest.mark.parametrize(('state', 'protect', 'expected'), [
    (0x10000, 0, 'free'), (0x2000, 0, 'reserved'),
    (0x1000, 0x104, 'committed_unreadable'), (0x1000, 4, 'committed_readable'),
])
def test_native_region_observation(state, protect, expected):
    from utils.drivers.coordinate_memory import ReadOnlyPosition

    memory = ReadOnlyPosition.__new__(ReadOnlyPosition)
    memory.handle = 1
    memory.kernel = Mock()
    def query(handle, address, output, size):
        output._obj.state = state
        output._obj.protect = protect
        return size
    memory.kernel.VirtualQueryEx.side_effect = query
    assert memory.reference_region(0x10000)['memory_region_status'] == expected


@pytest.mark.parametrize(('failure', 'expected'), [
    ('owner_header', ['owner_header']),
    ('field_address', ['owner_header', 'field_address']),
    ('field_pointer', ['owner_header', 'field_address', 'field_pointer']),
    ('array_header', ['owner_header', 'field_address', 'field_pointer', 'array_header']),
    ('array_length', ['owner_header', 'field_address', 'field_pointer', 'array_header', 'array_length']),
])
def test_collision_trace_stops_at_failed_read(failure, expected):
    from utils.core.scene_read import SceneReferenceTrace
    from utils.drivers.mono_scene import SceneMetadata

    def result(stage, value):
        if stage == failure:
            raise SceneReadIssue('unavailable', kind='invalid_reference')
        return value

    meta = SceneMetadata(Mock(), 1)
    meta.obj = lambda address: result('owner_header' if address == 10 else 'array_header',
                                      {'address': 30, 'kind': 4})
    meta.field = lambda *_: result('field_address', {'code': 29, 'flags': 0, 'offset': 16})
    meta.pointer = lambda _: result('field_pointer', 20)
    meta.data = lambda *_: result('array_length', (2**64 - 1).to_bytes(8, 'little'))
    trace = SceneReferenceTrace(7, (1, 2), 5, 10)
    with pytest.raises(SceneReadIssue):
        meta.array_refs(meta.ref(10, 'colliders', trace=trace), trace=trace)
    assert [step['stage'] for step in trace.steps] == expected
    assert trace.steps[-1]['state'] == 'failed'
    assert all(step['seconds'] >= 0 for step in trace.steps)


def test_collision_trace_distinguishes_empty_link_and_replacement():
    from utils.core.scene_read import SceneReferenceTrace
    trace = SceneReferenceTrace(7, (1, 2), 5, 10)
    assert trace.step('field_pointer', lambda: 0) == 0
    assert trace.step('field_pointer', lambda: 20) == 20
    assert trace.steps[0]['state'] == 'empty'
    assert trace.steps[1]['replacement'] is True


@pytest.mark.parametrize('fault', ['invalid_reference', 'short_read', 'read_bounds'])
def test_replaced_link_can_recover_read_failure_only_after_scope_proof(fault):
    current = [10]
    calls = []
    guard = Mock()
    def read(at):
        calls.append(at)
        if at == 10:
            current[0] = 20
            if fault == 'invalid_reference':
                raise SceneReadIssue('bad reference', kind=fault)
            raise ValueError('场景数据正在变化' if fault == 'short_read' else '场景数据范围无效')
        return ['fresh']
    budget = RecoveryBudget()
    assert read_linked_collection(lambda: current[0], read, guard, budget,
                                  stage='managed_collision_array') == ['fresh']
    assert calls == [10, 20] and budget.retries == 1
    guard.assert_called()


def test_changed_link_does_not_allow_recovery_in_another_world():
    current = [10]
    read = Mock()
    def fail(at):
        current[0] = 20
        raise SceneReadIssue('bad reference', kind='invalid_reference')
    read.side_effect = fail
    guard = Mock(side_effect=SceneReadIssue('new scene', kind='scope_changed'))
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(lambda: current[0], read, guard, RecoveryBudget(), stage='test')
    assert caught.value.kind == 'scope_changed' and read.call_count == 1


def test_recovery_does_not_read_a_failed_array_if_link_reverts():
    resolve = Mock(side_effect=[10, 20, 10])
    read = Mock(side_effect=SceneReadIssue('bad reference', kind='invalid_reference'))
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(resolve, read, Mock(), RecoveryBudget(),
                               stage='managed_collision_array')
    assert caught.value.kind == 'invalid_reference'
    read.assert_called_once_with(10)


def test_temporarily_invalid_owner_link_needs_a_valid_witness_and_scope_proof():
    resolve = Mock(side_effect=[SceneReadIssue('invalid',kind='invalid_reference'),20,20,20])
    guard, read, budget = Mock(), Mock(return_value=['fresh']), RecoveryBudget()
    assert read_linked_collection(resolve,read,guard,budget,stage='managed_collision_array') == ['fresh']
    assert read.call_args.args == (20,) and budget.retries == 1
    guard.assert_called()


def test_invalid_link_witness_in_a_changed_scope_never_reaches_payload():
    resolve = Mock(side_effect=[SceneReadIssue('invalid',kind='invalid_reference'),20])
    read = Mock()
    guard = Mock(side_effect=SceneReadIssue('changed',kind='scope_changed'))
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(resolve,read,guard,RecoveryBudget(),stage='managed_collision_array')
    assert caught.value.kind == 'scope_changed'
    read.assert_not_called()


def test_a_stable_invalid_owner_link_remains_unverified():
    resolve = Mock(side_effect=SceneReadIssue('invalid',kind='invalid_reference'))
    read, budget = Mock(), RecoveryBudget()
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(resolve,read,Mock(),budget,stage='managed_collision_array')
    assert not caught.value.retryable and not caught.value.scope_verified
    assert budget.retries == 0
    read.assert_not_called()


def test_slow_link_witness_consumes_the_shared_recovery_time_budget():
    now = [0.]
    calls = [0]
    def resolve():
        calls[0] += 1
        if calls[0] == 1:
            raise SceneReadIssue('invalid',kind='invalid_reference')
        now[0] += .2
        return 20
    budget = RecoveryBudget(clock=lambda:now[0],max_seconds=.1)
    read = Mock()
    with pytest.raises(SceneReadIssue):
        read_linked_collection(resolve,read,Mock(),budget,stage='managed_collision_array')
    assert calls[0] == 2 and budget.spent == pytest.approx(.2)
    read.assert_not_called()


def test_cancellation_during_link_witness_prevents_payload_and_retry():
    stopped, calls = [False], [0]
    def resolve():
        calls[0] += 1
        if calls[0] == 1:
            raise SceneReadIssue('invalid',kind='invalid_reference')
        stopped[0] = True
        return 20
    read = Mock()
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(resolve,read,Mock(),RecoveryBudget(cancelled=lambda:stopped[0]),
                               stage='managed_collision_array')
    assert caught.value.kind == 'cancelled'
    read.assert_not_called()


def test_exhausted_budget_does_not_even_probe_a_failed_owner_link():
    resolve=Mock(side_effect=SceneReadIssue('invalid',kind='invalid_reference'))
    read=Mock()
    with pytest.raises(SceneReadIssue):
        read_linked_collection(resolve,read,Mock(),RecoveryBudget(max_retries=0),stage='test')
    assert resolve.call_count==1
    read.assert_not_called()


@pytest.mark.parametrize('replacement', [0, 10])
def test_invalid_or_unchanged_link_keeps_failure_and_never_retries(replacement):
    current = [10]
    def fail(at):
        current[0] = replacement
        raise SceneReadIssue('bad reference', kind='invalid_reference')
    read = Mock(side_effect=fail)
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(lambda: current[0], read, Mock(), RecoveryBudget(), stage='test')
    assert caught.value.kind == 'invalid_reference' and not caught.value.retryable
    assert read.call_count == 1


def test_bad_layout_is_not_promoted_even_if_a_link_changed():
    current = [10]
    def fail(at):
        current[0] = 20
        raise ValueError('场景集合布局无效')
    read = Mock(side_effect=fail)
    with pytest.raises(ValueError):
        read_linked_collection(lambda: current[0], read, Mock(), RecoveryBudget(), stage='test')
    assert read.call_count == 1


@pytest.mark.parametrize(('witness', 'outcome'), [
    (0, 'link_empty'),
    (10, 'link_unchanged'),
    (ValueError('unreadable'), 'witness_failed'),
])
def test_failed_collision_read_reports_link_witness_without_authorizing_retry(witness, outcome):
    resolve = Mock(side_effect=[10, witness])
    read = Mock(side_effect=SceneReadIssue('invalid', kind='invalid_reference'))
    guard = Mock()
    observations = {}
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(resolve, read, guard, RecoveryBudget(),
                               stage='managed_collision_array', observations=observations)
    assert caught.value.kind == 'invalid_reference'
    expected = {outcome: 1, 'array_payload_failed': 1}
    if outcome == 'witness_failed':
        expected['witness_error:unknown'] = 1
    assert observations == {'managed_collision_array': expected}
    read.assert_called_once_with(10)
    guard.assert_not_called()


def nav_reader(error=None):
    source = Mock(spec=NavigationSceneMap)
    source.navigation_only = True
    source.last_read_stats = {}
    source.snapshot.side_effect = error
    source.snapshot.return_value = {'state':'ready', 'world_state':'ready',
                                    'objects':[], 'colliders':[], 'navigation_only':True}
    return source


@pytest.mark.parametrize('kind,code', [('invalid_reference', None), ('unknown', 'read_bounds'),
                                      ('unknown', 'short_read')])
def test_known_navigation_reference_failure_restarts_fresh_without_two_second_idle(kind, code):
    issue = SceneReadIssue('unavailable', kind=kind, stage='managed_collision_array')
    if code:
        issue.code = code
    broken, fresh = nav_reader(issue), nav_reader()
    factory = Mock(side_effect=[broken, fresh])
    worker = SceneMapWorker('profile', factory=factory)
    try:
        worker.set_pid(7)
        worker.poll_once()
        broken.close.assert_called_once()
        assert worker.reader is None and worker.navigation_snapshot(7) is None
        assert worker.retry_delay == pytest.approx(.1)
        assert worker.transient_failures == 0  # It was NOT reclassified as a proven mutation.
        assert worker.take_latest()['issue']['kind'] == kind
        worker.poll_once()
        assert worker.reader is fresh and worker.navigation_snapshot(7)
        assert worker.fresh_failures == 0
    finally:
        worker.close()


@pytest.mark.parametrize('kind,stage,code', [('scope_changed','managed_collision_array',None),
    ('invalid_reference','registry_start',None), ('io_error','managed_collision_array',None),
    ('unknown','managed_collision_array','collection_layout'), ('unknown','snapshot',None)])
def test_other_failures_keep_existing_close_backoff_and_no_old_snapshot(kind, stage, code):
    issue = SceneReadIssue('unavailable', kind=kind, stage=stage)
    if code:
        issue.code = code
    source = nav_reader(issue)
    worker = SceneMapWorker('profile', factory=lambda *args:source)
    try:
        worker.set_pid(7)
        worker.poll_once()
        assert worker.retry_delay == 2. and worker.navigation_snapshot(7) is None
        source.close.assert_called_once()
    finally:
        worker.close()


def test_repeated_fresh_restarts_are_bounded_and_pid_change_resets_backoff():
    issue = SceneReadIssue('unavailable', kind='invalid_reference', stage='managed_collision_array')
    worker = SceneMapWorker('profile', factory=lambda *args:nav_reader(issue))
    try:
        worker.set_pid(7)
        delays = []
        for _ in range(9):
            worker.poll_once()
            delays.append(worker.retry_delay)
        assert delays == pytest.approx([.1,.2,.4,.8,1.6,2.,2.,2.,2.])
        worker.set_pid(8)
        worker.poll_once()
        assert worker.fresh_failures == 1 and worker.retry_delay == pytest.approx(.1)
    finally:
        worker.close()


def test_display_reader_does_not_gain_navigation_fast_restart_policy():
    source = nav_reader(SceneReadIssue('bad',kind='invalid_reference',stage='managed_collision_array'))
    source.navigation_only = False
    worker = SceneMapWorker('profile',factory=lambda *args:source)
    try:
        worker.set_pid(7);worker.poll_once()
        assert worker.retry_delay == 2.
    finally:
        worker.close()


def test_late_fresh_failure_cannot_change_new_pid_recovery_policy():
    source=nav_reader()
    worker=SceneMapWorker('profile',factory=lambda *args:source)
    def fail_after_switch():
        worker.set_pid(8)
        raise SceneReadIssue('invalid',kind='invalid_reference',stage='managed_collision_array')
    source.snapshot.side_effect=fail_after_switch
    try:
        worker.set_pid(7);worker.poll_once()
        assert worker.fresh_failures==0 and worker.retry_delay==2.
        assert worker.navigation_snapshot(8) is None
    finally:
        worker.close()


def test_faster_fresh_query_shortens_supply_gap_without_using_the_failed_snapshot():
    from tests.unit.test_scene_read_recovery import quality_scene
    def run(legacy_delay):
        now=[10.]
        step=[0]
        def snapshot():
            step[0]+=1
            if step[0]==2:
                now[0]+=.5
                raise SceneReadIssue('invalid',kind='invalid_reference',stage='managed_collision_array')
            now[0]+=2.
            return quality_scene(now[0])
        sources=[]
        def factory(*args):
            source=nav_reader();source.snapshot.side_effect=snapshot;sources.append(source)
            return source
        worker=SceneMapWorker('profile',factory=factory,clock=lambda:now[0])
        try:
            worker.set_pid(7);worker.poll_once()
            assert worker.navigation_snapshot(7)
            worker.poll_once()
            assert worker.navigation_snapshot(7) is None  # Even its old TTL was not exhausted.
            sources[0].close.assert_called_once()
            now[0]+=2. if legacy_delay else worker.retry_delay
            worker.poll_once()
            assert worker.navigation_snapshot(7)
            return worker.navigation_metrics(7)['snapshot_longest_gap_seconds']
        finally:worker.close()
    assert run(True)==pytest.approx(4.)
    assert run(False)==pytest.approx(2.1)
