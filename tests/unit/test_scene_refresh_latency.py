"""Full-scene cadence and end-of-read liveness, with no game input."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.core import thresholds as policy
from utils.drivers.mono_scene import MemorySceneMap
from utils.ui.scene_map_worker import SceneMapWorker


class Wake:
    def __init__(self, worker): self.worker, self.waits = worker, []
    def clear(self): pass
    def wait(self, delay):
        self.waits.append(delay)
        self.worker.stop.set()


@pytest.mark.parametrize('duration', [.04, .9])
def test_complete_scan_cost_is_not_followed_by_another_full_refresh_interval(duration):
    now = [0.]
    worker = SceneMapWorker('profile', clock=lambda: now[0])
    worker.wake = Wake(worker)
    def poll():
        now[0] += duration
        return True
    worker.poll_once = poll
    worker.run()
    assert worker.wake.waits == [pytest.approx(max(policy.COORD_SCENE_MIN_IDLE_SECONDS,
                                                  policy.COORD_SCENE_REFRESH_SECONDS-duration))]
    assert worker.wake.waits[0] < 1.


def test_failed_reads_keep_backoff_instead_of_reopening_a_handle_at_map_frequency():
    worker = SceneMapWorker('profile', clock=lambda: 0.)
    worker.wake = Wake(worker)
    worker.poll_once = lambda: False
    worker.run()
    assert worker.wake.waits == [policy.COORD_MONITOR_RETRY_SECONDS]


def test_object_disappearing_while_collisions_are_read_is_not_published():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = SimpleNamespace(dictionary=lambda registry: [2], ref=lambda *args: 22,
                                 obj=lambda obj: {'name': 'LevelEntityComponent'})
    reader.object_category = lambda *args: 'butterfly'
    world = Mock(return_value=([4, 5, 6], None, None))
    records = [(1, 11, {'category': 'butterfly', 'position': [0, 0, 0]}),
               (2, 22, {'category': 'butterfly', 'position': [1, 0, 0]})]
    result = reader.refresh_live_objects(records, 100, world)
    assert result == [{'category': 'butterfly', 'position': [4, 5, 6]}]
    world.assert_called_once_with(2)


def test_reused_or_retyped_dynamic_entity_does_not_retain_its_old_icon():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = SimpleNamespace(dictionary=lambda registry: [1], ref=lambda *args: 11,
                                 obj=lambda obj: {'name': 'LevelEntityComponent'})
    reader.object_category = lambda *args: 'other'
    assert reader.refresh_live_objects([(1, 11, {'category': 'insect'})], 100, Mock()) == []
    reader.meta.ref = lambda *args: 99
    reader.object_category = Mock()
    assert reader.refresh_live_objects([(1, 11, {'category': 'insect'})], 100, Mock()) == []
    reader.object_category.assert_not_called()


def test_fixed_objects_survive_recheck_without_unnecessary_transform_reads():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = SimpleNamespace(dictionary=lambda registry: [1])
    world = Mock()
    item = {'category': 'tree', 'position': [1, 2, 3]}
    assert reader.refresh_live_objects([(1, 11, item)], 100, world) == [item]
    world.assert_not_called()


def test_unreadable_dynamic_object_is_dropped_without_losing_other_objects():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = SimpleNamespace(dictionary=lambda registry: [1, 2], ref=Mock(side_effect=OSError('gone')))
    tree = {'category': 'tree'}
    assert reader.refresh_live_objects([(1, 11, {'category': 'insect'}), (2, 22, tree)], 100, Mock()) == [tree]
