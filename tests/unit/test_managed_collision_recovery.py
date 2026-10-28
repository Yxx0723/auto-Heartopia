"""Synthetic managed collision graphs; no process handles or real input."""
from unittest.mock import Mock

import pytest

from tests.unit.test_guidance_planar_scene import plane_reader
from utils.core.scene_read import RecoveryBudget, SceneReadIssue


@pytest.mark.parametrize('collection,stage', [(1000,'managed_group_colliders'),
    (2001,'managed_stream_collisions'),(2003,'managed_collision_array')])
def test_actual_snapshot_recovers_one_verified_managed_collection_mutation(collection,stage):
    reader=plane_reader();m=reader.meta
    method=m.array_refs if collection==2003 else m.dictionary
    original=method.side_effect or (lambda _:method.return_value)
    calls=[]
    def read(at):
        if at==collection:
            calls.append(at)
            if len(calls)==1:raise SceneReadIssue('changing',kind='mutation',retryable=True)
        return original(at)
    method.side_effect=read
    snapshot=reader.snapshot()
    assert snapshot['quality']['navigation_eligible']
    assert len(snapshot['colliders'])==1
    assert reader.last_read_stats['retries']==1
    assert len(calls)>=2


def test_unknown_managed_failure_is_localized_and_does_not_retry_or_expose_text():
    from utils.core.scene_read import issue_summary
    reader=plane_reader();original=reader.meta.dictionary.side_effect;calls=[]
    def read(at):
        if at==1000:
            calls.append(at);raise ValueError('private pointer 0xDEADBEEF')
        return original(at)
    reader.meta.dictionary.side_effect=read
    with pytest.raises(ValueError) as caught:reader.snapshot()
    issue=issue_summary(caught.value)
    assert issue['stage']=='managed_group_colliders'
    assert issue['kind']=='unknown' and not issue['retryable']
    assert 'DEADBEEF' not in str(issue)
    assert len(calls)==1
    assert reader.last_read_stats['failure']['stage']=='managed_group_colliders'


def test_scene_change_after_managed_mutation_prevents_retry():
    reader=plane_reader();original=reader.meta.dictionary.side_effect;calls=[]
    def read(at):
        if at==1000:
            calls.append(at)
            reader.meta.static_ref.return_value=88
            raise SceneReadIssue('changing',kind='mutation',retryable=True)
        return original(at)
    reader.meta.dictionary.side_effect=read
    with pytest.raises(SceneReadIssue) as caught:reader.snapshot()
    assert caught.value.kind=='scope_changed'
    assert len(calls)==1


def test_recycled_group_owner_after_mutation_cannot_be_retried_as_same_scope():
    reader=plane_reader();m=reader.meta;original=m.dictionary.side_effect
    refs=m.ref.side_effect
    changed=[False]
    def ref(at,name):
        if changed[0] and (at,name)==(2,'_hierarchyObject'):return 999
        return refs(at,name)
    def read(at):
        if at==1000:
            changed[0]=True
            raise SceneReadIssue('changing',kind='mutation',retryable=True)
        return original(at)
    m.ref.side_effect=ref;m.dictionary.side_effect=read
    with pytest.raises(SceneReadIssue) as caught:reader.snapshot()
    assert caught.value.kind=='scope_changed'


def test_linked_field_replacement_discards_old_collection_and_resolves_again():
    from utils.core.scene_read import read_linked_collection
    current=[10];read_calls=[]
    def read(at):
        read_calls.append(at)
        if at==10:current[0]=20;return ['old']
        return ['new']
    guard=Mock();budget=RecoveryBudget()
    assert read_linked_collection(lambda:current[0],read,guard,budget,stage='managed_collision_array')==['new']
    assert read_calls==[10,20] and budget.retries==1
    guard.assert_called()


def test_successful_linked_read_does_not_run_expensive_scene_guard():
    from utils.core.scene_read import read_linked_collection
    guard=Mock()
    assert read_linked_collection(lambda:10,lambda at:[1],guard,RecoveryBudget(),stage='test')==[1]
    guard.assert_not_called()


@pytest.mark.parametrize('kind',['unknown','invalid_reference','scope_changed','io_error'])
def test_non_mutation_linked_failures_never_retry(kind):
    from utils.core.scene_read import read_linked_collection
    read=Mock(side_effect=SceneReadIssue('failure',kind=kind))
    with pytest.raises(SceneReadIssue):
        read_linked_collection(lambda:10,read,Mock(),RecoveryBudget(),stage='test')
    assert read.call_count==1


def test_continuous_linked_mutation_obeys_attempt_and_shared_time_budget():
    from utils.core.scene_read import read_linked_collection
    budget=RecoveryBudget(max_retries=1)
    def changing():return Mock(side_effect=SceneReadIssue('changing',kind='mutation',retryable=True))
    first=changing()
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(lambda:10,first,Mock(),budget,stage='managed_stream_collisions')
    assert first.call_count==2 and budget.retries==1 and caught.value.scope_verified
    second=changing()
    with pytest.raises(SceneReadIssue):read_linked_collection(lambda:20,second,Mock(),budget,stage='test')
    assert second.call_count==1


def test_cancel_during_collection_does_not_retry_or_accept_result():
    from utils.core.scene_read import read_linked_collection
    stopped=[False]
    def read(at):stopped[0]=True;return [1]
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(lambda:10,read,Mock(),RecoveryBudget(cancelled=lambda:stopped[0]),stage='test')
    assert caught.value.kind=='cancelled'


def test_resolver_mutation_requires_real_scope_verification_before_retry():
    from utils.core.scene_read import read_linked_collection
    resolve=Mock(side_effect=[SceneReadIssue('changing',kind='mutation',retryable=True),10])
    guard=Mock(side_effect=SceneReadIssue('changed world',kind='scope_changed'))
    with pytest.raises(SceneReadIssue) as caught:
        read_linked_collection(resolve,lambda at:[1],guard,RecoveryBudget(),stage='test')
    assert caught.value.kind=='scope_changed' and resolve.call_count==1


def test_slow_exceptional_scope_proof_consumes_shared_recovery_time():
    from utils.core.scene_read import read_linked_collection
    now=[0.];budget=RecoveryBudget(clock=lambda:now[0],max_seconds=.1)
    read=Mock(side_effect=SceneReadIssue('changing',kind='mutation',retryable=True))
    def guard():now[0]+=.2
    with pytest.raises(SceneReadIssue):read_linked_collection(lambda:10,read,guard,budget,stage='test')
    assert read.call_count==1 and budget.spent==pytest.approx(.2)


def test_exhausted_verified_mutation_retains_reader_but_revokes_navigation():
    from utils.ui.scene_map_worker import SceneMapWorker
    reader=plane_reader();worker=SceneMapWorker('profile',factory=lambda *a:reader)
    worker.set_pid(7);worker.poll_once();assert worker.navigation_snapshot(7)
    original=reader.meta.dictionary.side_effect
    def read(at):
        if at==1000:raise SceneReadIssue('changing',kind='mutation',retryable=True)
        return original(at)
    reader.meta.dictionary.side_effect=read
    worker.poll_once()
    assert worker.navigation_snapshot(7) is None and worker.reader is reader
    assert worker.retry_delay==pytest.approx(.1)
    assert reader.last_read_stats['failure']['scope_verified']
    worker.close()


def test_shape_failure_stays_partial_and_has_bounded_safe_detail():
    reader=plane_reader(size=(-1.,2.,3.));scene=reader.snapshot()
    assert not scene['quality']['navigation_eligible']
    assert scene['quality']['managed_issues'][0]['stage']=='managed_shape_data'
    assert len(scene['quality']['managed_issues'])<=16


def test_stable_bad_collection_layout_is_not_promoted_to_transient():
    from utils.core.scene_read import issue_summary
    reader=plane_reader();original=reader.meta.dictionary.side_effect
    def read(at):
        if at==2001:raise ValueError('场景集合布局无效')
        return original(at)
    reader.meta.dictionary.side_effect=read
    with pytest.raises(ValueError) as caught:reader.snapshot()
    issue=issue_summary(caught.value)
    assert issue['code']=='collection_layout' and issue['stage']=='managed_stream_collisions'
    assert not issue['retryable'] and reader.last_read_stats['retries']==0
    assert reader.last_read_stats['failure']['code']=='collection_layout'


def test_shape_issues_are_also_in_the_diagnostic_read_stats():
    reader=plane_reader(size=(-1.,2.,3.));scene=reader.snapshot()
    assert reader.last_read_stats['managed_issues']==scene['quality']['managed_issues']


def test_real_snapshot_re_resolves_replaced_group_dictionary_before_using_items():
    reader=plane_reader();m=reader.meta;original_ref=m.ref.side_effect;original_read=m.dictionary.side_effect
    moved=[False];calls=[]
    def ref(at,name):
        if moved[0] and (at,name)==(22,'_colliders'):return 1100
        return original_ref(at,name)
    def read(at):
        if at==1000:
            calls.append(at);moved[0]=True;return [9999]  # Must never use these stale items.
        if at==1100:calls.append(at);return [1001]
        return original_read(at)
    m.ref.side_effect=ref;m.dictionary.side_effect=read
    assert reader.snapshot()['quality']['navigation_eligible']
    assert calls==[1000,1100] and reader.last_read_stats['retries']==1


def test_stream_anchor_replacement_after_mutation_aborts_old_stream():
    reader=plane_reader();m=reader.meta;original_ref=m.ref.side_effect;original_read=m.dictionary.side_effect
    moved=[False];calls=[]
    def ref(at,name):
        if moved[0] and (at,name)==(1001,'_stream'):return 9000
        return original_ref(at,name)
    def read(at):
        if at==2001:
            calls.append(at);moved[0]=True
            raise SceneReadIssue('changing',kind='mutation',retryable=True)
        return original_read(at)
    m.ref.side_effect=ref;m.dictionary.side_effect=read
    with pytest.raises(SceneReadIssue) as caught:reader.snapshot()
    assert caught.value.kind=='scope_changed' and len(calls)==1


def test_removed_collision_membership_cannot_authorize_array_retry():
    reader=plane_reader();m=reader.meta;original_read=m.dictionary.side_effect;removed=[False]
    def read(at):
        if removed[0] and at==2001:return []
        return original_read(at)
    def array(at):
        removed[0]=True;raise SceneReadIssue('changing',kind='mutation',retryable=True)
    m.dictionary.side_effect=read;m.array_refs.side_effect=array
    with pytest.raises(SceneReadIssue) as caught:reader.snapshot()
    assert caught.value.kind=='scope_changed' and m.array_refs.call_count==1
