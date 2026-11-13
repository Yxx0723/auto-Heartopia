"""R-02D-2 regression: collision-stream membership changes during owner re-check.

The 2026-11-13 23:28:19.921 reader discard came from an owner_header invalid
reference whose re-check saw the membership change. The old narrow path could
only recover when the collection was completely stable, so it kept the
un-retryable invalid_reference and discarded the reader. These tests pin the
bounded re-sampling, generation check and integrity gate of the minimal fix.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.core.scene_read import SceneReadIssue
from utils.drivers.mono_scene import MemorySceneMap

STREAM = 0x1000
OWNER = 0x2000
COLLISION_REF = 0x3000
STABLE_STAMP = (7, 12, 0, 0x4000)
OTHER_STAMP = (8, 12, 0, 0x4000)


def mutation(code='collision_membership_changed'):
    issue = SceneReadIssue('托管碰撞流成员变化', kind='mutation',
                           stage='managed_parent_recheck', retryable=True, scope_verified=True)
    issue.code = code
    return issue


def scope_changed():
    return SceneReadIssue('场景范围变化', kind='scope_changed',
                          stage='managed_parent_recheck')


def fixture(samples, verify_errors=(), terminal_kind='invalid_reference'):
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.read_budget = Mock()
    reader.meta = Mock()
    reader.meta.ref.return_value = COLLISION_REF
    pending = list(samples)
    reader.meta.dictionary.side_effect = lambda *args, **kwargs: (
        pending.pop(0) if pending else ([], STABLE_STAMP))
    errors = list(verify_errors)

    def verify(stream):
        if errors:
            err = errors.pop(0)
            if err is not None:
                raise err

    verify = Mock(side_effect=verify)
    trace = SimpleNamespace(context={
        'owner': 'test-owner',
        'terminal_issue': {'kind': terminal_kind, 'stage': 'owner_header'},
        'collision_stream_membership': 'unknown', 'scene_generation': 'unknown',
        'recovery_decision': 'existing_policy'})
    return reader, verify, trace


def test_membership_change_during_recheck_then_owner_removed():
    """The exact 23:28:19.921 shape: change at re-check, owner gone, stable set."""
    reader, verify, trace = fixture(
        [([], STABLE_STAMP), ([], STABLE_STAMP)],
        verify_errors=[mutation()])
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is True
    assert trace.context['recovery_decision'] == 'rescan_verified_stream'
    assert trace.context['membership_recovery'] == 'owner_removed'
    assert trace.context['collision_stream_membership'] == 'absent'
    assert trace.context['membership_stability'] == 'stable'
    assert trace.context['scene_generation'] != 'unknown'
    assert trace.context['scene_scope'] == 'membership_changed_at_recheck'


def test_stable_membership_with_owner_already_removed():
    reader, verify, trace = fixture([([], STABLE_STAMP), ([], STABLE_STAMP)])
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is True
    assert trace.context['recovery_decision'] == 'rescan_verified_stream'


def test_continuous_membership_change_keeps_integrity_gate():
    samples = [([OWNER], (n, 12, 0, 0x4000)) for n in range(8)]
    reader, verify, trace = fixture(samples)
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is False
    assert trace.context['membership_recovery'] == 'unstable'
    assert trace.context['recovery_decision'] == 'existing_policy'


def test_owner_still_present_is_not_treated_as_removed():
    reader, verify, trace = fixture(
        [([OWNER], STABLE_STAMP), ([OWNER], STABLE_STAMP)])
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is False
    assert trace.context['membership_recovery'] == 'owner_still_present'
    assert trace.context['collision_stream_membership'] == 'present'
    assert trace.context['recovery_decision'] == 'existing_policy'


def test_address_reuse_keeps_integrity_gate():
    """Owner address reappears in a new membership: never accept local cleanup."""
    reader, verify, trace = fixture(
        [([OWNER], STABLE_STAMP), ([OWNER], STABLE_STAMP)],
        verify_errors=[scope_changed()])
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is False
    assert trace.context['membership_recovery'] == 'owner_still_present'
    assert trace.context['collision_stream_membership'] == 'present'
    assert trace.context['recovery_decision'] == 'existing_policy'


def test_unknown_failure_keeps_existing_policy():
    reader, verify, trace = fixture([([], STABLE_STAMP)], terminal_kind='unknown')
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is False
    assert trace.context['recovery_decision'] == 'existing_policy'


def test_unverified_read_error_never_recovers():
    reader, verify, trace = fixture([([], STABLE_STAMP)],
                                    verify_errors=[SceneReadIssue('unverified', kind='unknown')])
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is False
    assert trace.context['recovery_decision'] == 'existing_policy'


def test_cancelled_issue_propagates():
    cancelled = SceneReadIssue('cancelled', kind='cancelled')
    reader, verify, trace = fixture([([], STABLE_STAMP)], verify_errors=[cancelled])
    with pytest.raises(SceneReadIssue) as caught:
        reader._collision_membership_recovery(trace, STREAM, OWNER, verify)
    assert caught.value is cancelled


def test_dictionary_read_failure_keeps_integrity_gate():
    reader, verify, trace = fixture([])
    reader.meta.dictionary.side_effect = OSError('read failed')
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is False
    assert trace.context['recovery_decision'] == 'existing_policy'


def test_verified_recovery_triggers_one_full_rescan_instead_of_reader_discard():
    """Local cleanup ends in the existing fresh _snapshot_attempt, not a discard."""
    reader, verify, trace = fixture([([], STABLE_STAMP), ([], STABLE_STAMP)],
                                    verify_errors=[mutation()])
    assert reader._collision_membership_recovery(trace, STREAM, OWNER, verify) is True
    attempts = []
    error = SceneReadIssue('invalid owner', kind='invalid_reference')

    def attempt():
        attempts.append(None)
        reader.collision_reference_failures = [dict(trace.context)]
        reader.last_read_stats = {'attempt': len(attempts)}
        raise error

    reader._snapshot_attempt = attempt
    with pytest.raises(SceneReadIssue) as caught:
        reader.snapshot()
    assert caught.value is error
    assert len(attempts) == 2  # one full rescan, no stale geometry merged
