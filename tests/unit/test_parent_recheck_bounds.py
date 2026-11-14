"""Bounded parent-transform recheck: per-stream settlement, proofs, cancellation.

The recheck may only shorten the wait for objects whose parent becomes readable
or that already left the live collision set. It never marks an unproven object
as complete, so these tests assert the raised collection cap together with the
unchanged blocking behaviour.
"""
import pytest

from unittest.mock import Mock

from utils.core import thresholds as policy
from utils.core.scene_read import RecoveryBudget, SceneReadIssue
from utils.drivers.mono_scene import MemorySceneMap, SceneMetadata


class FakeMeta:
    """Minimal stand-in for the two-stamp dictionary reads of the recheck."""

    def __init__(self, members=None, unstable=False):
        self.members = {stream: set(items) for stream, items in (members or {}).items()}
        self.unstable = unstable
        self.stage = 'managed_parent_recheck'
        self.dictionary_reads = []

    def ref(self, owner, field):
        return (owner, field)

    def dictionary(self, reference, with_stamp=False):
        stream, _ = reference
        self.dictionary_reads.append(stream)
        members = set(self.members.get(stream, ()))
        if self.unstable:
            # A collection that keeps changing can never prove a removal.
            members.add(object())
            stamp = object()
        else:
            stamp = len(members)
        return (members, stamp) if with_stamp else members


def reader(cancelled=lambda: False, members=None, unstable=False):
    instance = MemorySceneMap.__new__(MemorySceneMap)
    instance.read_budget = RecoveryBudget(cancelled=cancelled)
    instance.check_parent_recovery = True
    instance.navigation_only = True
    instance.managed_parent_recovered = False
    instance.managed_parent_recheck_stats = None
    instance.phase_times, instance.phase_name, instance.phase_started = {}, None, 0.
    instance.meta = FakeMeta(members, unstable=unstable)
    return instance


def candidates(count, stream=10):
    """Distinct colliders of one stream: (stream, collision, collider, class)."""
    return [(stream, 1000 + index, 2000 + index, 3000 + index) for index in range(count)]


def prover(verdicts):
    """Stand-in for _live_managed_parent; records which colliders were proved."""
    queue, seen = list(verdicts), []

    def prove(*args):
        seen.append(args[2])
        return queue.pop(0) if queue else False

    prove.seen = seen
    return prove


def run(instance, items, verify_stream=None):
    return instance._run_parent_recheck(items, Mock(), verify_stream or Mock(), {}, set())


def test_small_batch_still_proves_every_candidate():
    batch = candidates(4)
    instance = reader(members={10: [item[1] for item in batch]})
    prove = prover([True] * 4)
    instance._live_managed_parent = prove
    stats = run(instance, batch)
    assert stats['candidates'] == 4 and stats['streams'] == 1
    assert (stats['removed'], stats['verified'], stats['unresolved']) == (0, 4, 4)
    assert stats['recovered'] is True and stats['seconds'] >= 0.
    assert len(prove.seen) == 4
    # A proven live parent needs rebuilt geometry, so the scan is re-read.
    assert instance.managed_parent_recovered is True
    assert instance.managed_parent_recheck_stats is stats


def test_large_batch_is_settled_per_stream_not_per_candidate():
    batch = candidates(16)
    instance = reader(members={10: [item[1] for item in batch]})
    prove = prover([True] * 16)
    instance._live_managed_parent = prove
    stats = run(instance, batch)
    # One stream costs one stable double read, not sixteen proofs.
    assert instance.meta.dictionary_reads == [10, 10]
    assert stats['unresolved'] == 16 and stats['verified'] == 0
    assert prove.seen == [] and stats['recovered'] is False
    assert instance.managed_parent_recovered is False


def test_removed_candidates_are_settled_without_a_proof():
    batch = candidates(16)
    instance = reader(members={10: []})
    prove = prover([])
    instance._live_managed_parent = prove
    stats = run(instance, batch)
    assert (stats['removed'], stats['verified'], stats['unresolved']) == (16, 0, 0)
    assert stats['recovered'] is True
    # An unloaded batch is already complete on this read; no rescan is needed.
    assert instance.managed_parent_recovered is False
    assert prove.seen == []


def test_partially_removed_batch_proves_only_the_live_members():
    batch = candidates(6)
    live = [batch[1], batch[4]]
    instance = reader(members={10: [item[1] for item in live]})
    prove = prover([True, True])
    instance._live_managed_parent = prove
    stats = run(instance, batch)
    assert (stats['removed'], stats['verified'], stats['unresolved']) == (4, 2, 2)
    assert prove.seen == [item[2] for item in live]
    assert stats['recovered'] is True and instance.managed_parent_recovered is True


def test_one_unproven_live_candidate_keeps_navigation_blocked():
    batch = candidates(4)
    instance = reader(members={10: [item[1] for item in batch]})
    instance._live_managed_parent = prover([True, True, False, True])
    stats = run(instance, batch)
    assert (stats['verified'], stats['candidates']) == (2, 4)
    assert stats['recovered'] is False
    assert instance.managed_parent_recovered is False


def test_first_unproven_candidate_stops_the_proof_early():
    batch = candidates(4)
    instance = reader(members={10: [item[1] for item in batch]})
    prove = prover([False, True, True, True])
    instance._live_managed_parent = prove
    stats = run(instance, batch)
    assert stats['verified'] == 0 and stats['recovered'] is False
    assert prove.seen == [batch[0][2]]


def test_several_streams_are_each_confirmed_once():
    batch = candidates(3, stream=10) + candidates(3, stream=11)
    instance = reader(members={10: [], 11: []})
    stats = run(instance, batch)
    assert stats['streams'] == 2 and stats['removed'] == 6
    assert sorted(instance.meta.dictionary_reads) == [10, 10, 11, 11]


def test_unstable_membership_never_counts_as_a_removal():
    instance = reader(unstable=True)
    with pytest.raises(SceneReadIssue) as caught:
        run(instance, candidates(4))
    assert caught.value.kind == 'mutation'
    assert caught.value.retryable is True and caught.value.scope_verified is True
    assert caught.value.code == 'collision_membership_unstable'
    assert instance.managed_parent_recheck_stats['recovered'] is False
    assert instance.managed_parent_recovered is False


def test_cancellation_during_the_membership_check_propagates():
    state = {'calls': 0}

    def cancelled():
        state['calls'] += 1
        return state['calls'] > 1

    instance = reader(cancelled=cancelled, members={10: []})
    with pytest.raises(SceneReadIssue) as caught:
        run(instance, candidates(4))
    assert caught.value.kind == 'cancelled'


def test_scope_change_during_the_membership_check_propagates_unretried():
    instance = reader(members={10: []})

    def verify_stream(stream):
        raise SceneReadIssue('场景已切换', kind='scope_changed', stage='managed_parent_recheck')

    with pytest.raises(SceneReadIssue) as caught:
        run(instance, candidates(4), verify_stream=verify_stream)
    assert caught.value.kind == 'scope_changed'
    assert instance.managed_parent_recovered is False


def test_membership_change_during_the_proof_is_not_a_partial_success():
    batch = candidates(2)
    instance = reader(members={10: [item[1] for item in batch]})

    def prove(*args):
        raise SceneReadIssue('集合变化', kind='mutation', stage='managed_parent_recheck',
                             retryable=True, scope_verified=True)

    instance._live_managed_parent = prove
    with pytest.raises(SceneReadIssue) as caught:
        run(instance, batch)
    assert caught.value.kind == 'mutation'
    assert instance.managed_parent_recheck_stats['recovered'] is False


def test_read_allowance_is_granted_per_stream_not_per_candidate():
    batch = candidates(6, stream=10) + candidates(6, stream=11) + candidates(6, stream=12)
    instance = reader(members={10: [], 11: [], 12: []})
    before_retries = instance.read_budget.max_retries
    before_seconds = instance.read_budget.max_seconds
    run(instance, batch)
    limit = 1 + instance.MEMBERSHIP_RECHECK_LIMIT
    assert instance.read_budget.max_retries == before_retries + 3 * limit
    assert instance.read_budget.max_seconds == pytest.approx(
        before_seconds + 3 * policy.COORD_SCENE_PARENT_RECHECK_SECONDS)


def test_empty_candidate_list_never_reports_recovery():
    instance = reader()
    instance.managed_parent_recovered = True
    stats = run(instance, [])
    assert stats['candidates'] == 0
    # An empty batch proves nothing, so it must not promote the scan.
    assert stats['recovered'] is False
    assert instance.managed_parent_recovered is False


def scene_metadata():
    return SceneMetadata.__new__(SceneMetadata)


def test_collection_cap_covers_a_whole_streaming_unload():
    assert policy.COORD_SCENE_PARENT_RECHECK_CANDIDATES >= 1024
    instance = reader()
    meta = scene_metadata()
    # The cap is a safety bound on collected objects, far above the largest
    # observed unload, so a real batch is never silently left uncollected.
    assert instance.parent_recheck_candidate(meta, candidates(1023)) is True
    assert instance.parent_recheck_candidate(meta, candidates(1024)) is False
    assert instance.parent_recheck_candidate(meta, candidates(1025)) is False


def test_proof_cap_stays_small():
    # Each proof re-reads the registry, so the object-by-object path stays a
    # short batch while the membership path handles the bulk.
    assert 1 <= policy.COORD_SCENE_PARENT_PROOF_CANDIDATES
    assert policy.COORD_SCENE_PARENT_PROOF_CANDIDATES < policy.COORD_SCENE_PARENT_RECHECK_CANDIDATES


def test_candidate_collection_stays_closed_while_recovery_is_off():
    instance = reader()
    instance.check_parent_recovery = False
    assert instance.parent_recheck_candidate(scene_metadata(), []) is False
    instance.check_parent_recovery = True
    instance.navigation_only = False
    assert instance.parent_recheck_candidate(scene_metadata(), []) is False


def test_grant_is_additive_and_ignores_negative_input():
    budget = RecoveryBudget()
    retries, seconds = budget.max_retries, budget.max_seconds
    budget.grant(16, .05)
    assert (budget.max_retries, budget.max_seconds) == (retries + 16, seconds + .05)
    budget.grant(-4, -.2)
    assert (budget.max_retries, budget.max_seconds) == (retries + 16, seconds + .05)
