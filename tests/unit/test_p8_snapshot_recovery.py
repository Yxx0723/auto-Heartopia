"""P8 recovery ordering and freshness regressions."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.core.scene_read import SceneReadIssue
from utils.drivers import mono_scene
from utils.drivers.mono_scene import NavigationSceneMap
from utils.ui.scene_map_worker import SceneMapWorker


@pytest.mark.parametrize('mode', ['valid', 'unproved', 'wrong_error', 'again', 'deadline'])
def test_stale_owner_after_parent_rescan_is_bounded(monkeypatch, mode):
    now = [10.]
    monkeypatch.setattr(mono_scene.time, 'monotonic', lambda: now[0])
    reader = NavigationSceneMap.__new__(NavigationSceneMap)
    attempts = []
    parent_change = SceneReadIssue('parent membership', kind='mutation')
    parent_change.fresh_parent_scan = True
    owner_error = SceneReadIssue('owner header', kind='invalid_reference', stage='managed_collision_array')

    def scan(*, check_parent_recovery=True):
        attempts.append(check_parent_recovery)
        reader.last_read_stats = {'ordinal': len(attempts)}
        if len(attempts) == 1:
            raise parent_change
        if len(attempts) == 2:
            reader.collision_reference_failures = [
                {'recovery_decision': 'rescan_verified_stream', 'owner': 'removed'}]
            if mode != 'unproved':
                reader.verified_collision_exit = (1, 2, 3, 4)
                reader.verified_collision_error = owner_error if mode != 'wrong_error' else parent_change
            if mode == 'deadline':
                now[0] += 6.
            raise owner_error
        assert reader.rescan_scope == (1, 2, 3, 4)
        assert reader.rescan_deadline == 16.
        if mode == 'again':
            raise owner_error
        return {'colliders': ['fresh']}

    reader._snapshot_attempt = scan
    if mode == 'valid':
        assert reader.snapshot() == {'colliders': ['fresh']}
    else:
        with pytest.raises(SceneReadIssue) as error:
            reader.snapshot()
        assert error.value is owner_error
    assert attempts == ([True, False, False] if mode in ('valid', 'again') else [True, False])
    assert reader.rescan_scope is None
    assert reader.last_read_stats['parent_membership_rescan']['ordinal'] == 1


def test_recovery_result_arriving_after_deadline_is_rejected(monkeypatch):
    now = [10.]
    monkeypatch.setattr(mono_scene.time, 'monotonic', lambda: now[0])
    reader = NavigationSceneMap.__new__(NavigationSceneMap)
    failure = SceneReadIssue('membership', kind='mutation')
    failure.fresh_parent_scan = True

    def scan(*, check_parent_recovery=True):
        if check_parent_recovery:
            raise failure
        now[0] = 16.001
        return {'colliders': ['late']}

    reader._snapshot_attempt = scan
    with pytest.raises(SceneReadIssue, match='超时'):
        reader.snapshot()
    assert reader.last_read_stats['scan_attempts'][-1]['issue']['kind'] == 'cancelled'


def test_scan_trace_correlates_previous_expiry_and_replacement():
    now = [10.]
    reader = SimpleNamespace(snapshot=lambda: {'state': 'ready'}, close=lambda: None)
    worker = SceneMapWorker({}, factory=lambda *_: reader, clock=lambda: now[0])
    worker.diagnostics = Mock()
    worker.set_pid(42)
    worker.poll_once()
    worker.navigation_snapshot(42)
    old_expiry = worker.supply.deadline
    now[0] = old_expiry + .25
    assert worker.navigation_snapshot(42) is None
    worker.poll_once()
    row = json.loads(worker.diagnostics.write.call_args.args[0]['message'])
    assert row['expired_before_publish']
    assert row['previous_snapshot_id'] == '1:1'
    assert row['snapshot_id'] == '1:2'
    assert row['previous_snapshot_expires_at_monotonic'] == old_expiry
    assert row['supply']['snapshot_unusable_by_reason']['expired'] == pytest.approx(.25)
