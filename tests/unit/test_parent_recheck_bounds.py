"""Bounded parent-transform recheck: batch size, proofs and cancellation.

The recheck may only shorten the wait for objects whose parent becomes readable.
It never marks an unproven object as complete, so these tests assert both the
raised batch limit and the unchanged blocking behaviour.
"""
import pytest

from unittest.mock import Mock

from utils.core import thresholds as policy
from utils.core.scene_read import RecoveryBudget, SceneReadIssue
from utils.drivers.mono_scene import MemorySceneMap, SceneMetadata


def reader(cancelled=lambda: False):
    instance = MemorySceneMap.__new__(MemorySceneMap)
    instance.read_budget = RecoveryBudget(cancelled=cancelled)
    instance.check_parent_recovery = True
    instance.navigation_only = True
    instance.managed_parent_recovered = False
    instance.managed_parent_recheck_stats = None
    return instance


def candidates(count):
    return [(10 + index, 20 + index, 30 + index, 40 + index) for index in range(count)]


def prover(verdicts):
    """Stand-in for _live_managed_parent; records which colliders were proved."""
    queue, seen = list(verdicts), []

    def prove(*args):
        seen.append(args[2])
        return queue.pop(0) if queue else False

    prove.seen = seen
    return prove


@pytest.mark.parametrize('count', [4, 5, 8, 16])
def test_batches_up_to_the_limit_prove_every_candidate(count):
    instance = reader()
    prove = prover([True] * count)
    instance._live_managed_parent = prove
    stats = instance._run_parent_recheck(candidates(count), Mock(), {}, set())
    assert stats['candidates'] == count and stats['verified'] == count
    assert stats['recovered'] is True and stats['seconds'] >= 0.
    assert len(prove.seen) == count
    assert instance.managed_parent_recovered is True
    assert instance.managed_parent_recheck_stats is stats


def scene_metadata():
    return SceneMetadata.__new__(SceneMetadata)


def test_seventeenth_candidate_is_not_collected():
    assert policy.COORD_SCENE_PARENT_RECHECK_CANDIDATES == 16
    instance = reader()
    meta = scene_metadata()
    # A full batch of 16 is accepted; the 17th slot is refused, so a larger
    # backlog keeps the existing scan-level retry instead of an unbounded proof.
    assert instance.parent_recheck_candidate(meta, candidates(15)) is True
    assert instance.parent_recheck_candidate(meta, candidates(16)) is False
    assert instance.parent_recheck_candidate(meta, candidates(17)) is False


def test_candidate_collection_stays_closed_while_recovery_is_off():
    instance = reader()
    instance.check_parent_recovery = False
    assert instance.parent_recheck_candidate(scene_metadata(), []) is False
    instance.check_parent_recovery = True
    instance.navigation_only = False
    assert instance.parent_recheck_candidate(scene_metadata(), []) is False


def test_one_unproven_candidate_keeps_navigation_blocked():
    instance = reader()
    instance._live_managed_parent = prover([True, True, False, True, True])
    stats = instance._run_parent_recheck(candidates(5), Mock(), {}, set())
    assert (stats['verified'], stats['candidates']) == (2, 5)
    assert stats['recovered'] is False
    assert instance.managed_parent_recovered is False


def test_first_unproven_candidate_stops_the_batch_early():
    instance = reader()
    prove = prover([False] + [True] * 15)
    instance._live_managed_parent = prove
    stats = instance._run_parent_recheck(candidates(16), Mock(), {}, set())
    assert stats['verified'] == 0 and stats['recovered'] is False
    assert prove.seen == [candidates(1)[0][2]]


def test_cancellation_during_the_recheck_propagates():
    state = {'calls': 0}

    def cancelled():
        state['calls'] += 1
        return state['calls'] > 1

    instance = reader(cancelled=cancelled)
    instance._live_managed_parent = prover([True] * 4)
    with pytest.raises(SceneReadIssue) as caught:
        instance._run_parent_recheck(candidates(4), Mock(), {}, set())
    assert caught.value.kind == 'cancelled'


def test_scope_change_during_the_recheck_propagates_unretried():
    instance = reader()

    def prove(*args):
        raise SceneReadIssue('场景已切换', kind='scope_changed', stage='managed_parent_recheck')

    instance._live_managed_parent = prove
    with pytest.raises(SceneReadIssue) as caught:
        instance._run_parent_recheck(candidates(4), Mock(), {}, set())
    assert caught.value.kind == 'scope_changed'
    assert instance.managed_parent_recovered is False


def test_membership_change_is_not_retried_as_a_partial_success():
    instance = reader()

    def prove(*args):
        raise SceneReadIssue('集合变化', kind='mutation', stage='managed_parent_recheck',
                             retryable=True, scope_verified=True)

    instance._live_managed_parent = prove
    with pytest.raises(SceneReadIssue) as caught:
        instance._run_parent_recheck(candidates(4), Mock(), {}, set())
    assert caught.value.kind == 'mutation'
    assert instance.managed_parent_recheck_stats['recovered'] is False


def test_read_allowance_grows_only_by_the_candidate_count():
    instance = reader()
    before_retries = instance.read_budget.max_retries
    before_seconds = instance.read_budget.max_seconds
    instance._live_managed_parent = prover([True] * 8)
    instance._run_parent_recheck(candidates(8), Mock(), {}, set())
    assert instance.read_budget.max_retries == before_retries + 8
    assert instance.read_budget.max_seconds == pytest.approx(
        before_seconds + policy.COORD_SCENE_PARENT_RECHECK_SECONDS)


def test_empty_candidate_list_never_reports_recovery():
    instance = reader()
    instance.managed_parent_recovered = True
    stats = instance._run_parent_recheck([], Mock(), {}, set())
    assert stats['candidates'] == 0
    # An empty batch proves nothing, so it must not promote the scan.
    assert stats['recovered'] is False
    assert instance.managed_parent_recovered is False


def test_grant_is_additive_and_ignores_negative_input():
    budget = RecoveryBudget()
    retries, seconds = budget.max_retries, budget.max_seconds
    budget.grant(16, .05)
    assert (budget.max_retries, budget.max_seconds) == (retries + 16, seconds + .05)
    budget.grant(-4, -.2)
    assert (budget.max_retries, budget.max_seconds) == (retries + 16, seconds + .05)
