"""Native collision rebuild and registry read accounting.

The native reader caches one layer's geometry, so a failed read must always
build a new reader on the next scan. These tests pin that behaviour and the
quality gate that keeps the scan non-navigable while the layer is missing.
"""
from unittest.mock import Mock

import pytest

from utils.core.scene_read import SceneReadIssue
from utils.drivers import mono_scene
from utils.drivers.mono_scene import MemorySceneMap


def scene_reader():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.memory = Mock(pid=7, base=1, profile={})
    reader.memory.snapshot.return_value = {
        'position': [1., 2., 3.],
        'paths': [{'trace': [{'field': '_hierarchy', 'pointer': 1}]}]}
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


def fake_native(monkeypatch, fail=False):
    created = []

    class FakeNative:
        def __init__(self, read, base, layout):
            created.append((base, layout))
            self.read, self.snapshot_calls = read, 0
            self.scene_key = ('town',)
            self.fail = fail

        def snapshot(self):
            self.snapshot_calls += 1
            if self.fail:
                raise ValueError('still switching')
            return [], {}

        def main_terrain_loaded(self):
            return False

    monkeypatch.setattr(mono_scene, 'NativeWorldCollisions', FakeNative)
    monkeypatch.setattr(mono_scene, 'native_scene_layout', lambda profile: 'layout')
    return created


def test_native_failure_discards_the_reader_and_records_the_reason():
    reader = scene_reader()
    reader.native.snapshot.side_effect = ValueError('pool changed')
    scene = reader.snapshot()
    assert scene['state'] == 'ready'
    assert scene['world_state'] == 'unavailable'
    assert reader.native is None
    stats = reader.native_rebuild_stats
    assert (stats['attempts'], stats['discarded'], stats['created']) == (1, 1, 0)
    assert 'pool changed' in stats['reason']
    assert stats['state'] == 'unavailable'


def test_native_failure_never_retries_the_same_reader_inside_one_scan():
    reader = scene_reader()
    native = reader.native
    native.snapshot.side_effect = ValueError('layer switching')
    reader.snapshot()
    # One read attempt per scan: the geometry cache belongs to the replaced
    # layer, so an in-place re-read would risk reusing stale geometry.
    assert native.snapshot.call_count == 1
    assert reader.native is None


def test_missing_native_layer_keeps_navigation_ineligible():
    reader = scene_reader()
    reader.native.snapshot.side_effect = ValueError('pool changed')
    quality = reader.snapshot()['quality']
    assert quality['navigation_eligible'] is False
    assert quality['parts']['native_collisions']['state'] == 'unavailable'
    assert quality['parts']['managed_collisions']['state'] == 'complete'


def test_next_scan_builds_a_fresh_reader_and_publishes_again(monkeypatch):
    reader = scene_reader()
    reader.native.snapshot.side_effect = ValueError('pool changed')
    reader.snapshot()
    created = fake_native(monkeypatch)
    reader.memory._module.return_value = (11, 22, 33)
    scene = reader.snapshot()
    assert scene['world_state'] == 'ready'
    assert len(created) == 1 and created[0] == (11, 'layout')
    assert reader.native_rebuild_stats['created'] == 1
    assert reader.native_rebuild_stats['discarded'] == 0
    assert scene['quality']['navigation_eligible'] is True


def test_repeated_failures_each_build_one_reader_only(monkeypatch):
    reader = scene_reader()
    created = fake_native(monkeypatch, fail=True)
    reader.memory._module.return_value = (11, 22, 33)
    reader.native = None
    for _ in range(3):
        scene = reader.snapshot()
        assert scene['world_state'] == 'unavailable'
        assert reader.native is None
    # Three scans, three fresh readers, never two inside one scan.
    assert len(created) == 3


def test_cancelled_scan_never_publishes_geometry():
    reader = scene_reader()
    reader.cancelled = lambda: True
    with pytest.raises(SceneReadIssue) as caught:
        reader.snapshot()
    assert caught.value.kind == 'cancelled'


def registry_reader():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = Mock(stage='registry_start')
    reader.meta.static_ref.return_value = 5
    reader.meta.dictionary.return_value = [1, 2]
    reader.memory = Mock()
    reader.memory.snapshot.return_value = {'paths': [{'trace': [{'field': '_hierarchy', 'pointer': 1}]}]}
    reader.local_hierarchy = lambda current: current['paths'][0]['trace'][0]['pointer']
    reader.registry_stats = {}
    return reader


def test_registry_reads_are_counted_once_per_attempt():
    from utils.core.scene_read import RecoveryBudget
    reader = registry_reader()
    budget = RecoveryBudget()
    assert reader.read_registry(5, 99, 1, budget) == [1, 2]
    assert reader.read_registry(5, 99, 1, budget) == [1, 2]
    stats = reader.registry_stats['registry_start']
    assert stats['reads'] == 2 and stats['retries'] == 0
    assert stats['seconds'] >= 0.


def test_registry_retry_inside_one_read_is_counted_not_hidden():
    from utils.core.scene_read import RecoveryBudget
    reader = registry_reader()
    reader.meta.dictionary.side_effect = [
        SceneReadIssue('集合变化', kind='mutation', retryable=True, scope_verified=True), [7]]
    budget = RecoveryBudget()
    assert reader.read_registry(5, 99, 1, budget) == [7]
    stats = reader.registry_stats['registry_start']
    assert stats['reads'] == 1 and stats['retries'] == 1


def test_registry_scope_change_is_never_retried_as_a_mutation():
    from utils.core.scene_read import RecoveryBudget
    reader = registry_reader()
    calls = {'n': 0}

    def snapshot():
        calls['n'] += 1
        # The guard sees a different scene root on the second look.
        return {'paths': [{'trace': [{'field': '_hierarchy', 'pointer': calls['n']}]}]}

    reader.memory.snapshot.side_effect = snapshot
    with pytest.raises(SceneReadIssue) as caught:
        reader.read_registry(5, 99, 1, RecoveryBudget())
    assert caught.value.kind == 'scope_changed'
    assert reader.registry_stats['registry_start']['retries'] == 0
