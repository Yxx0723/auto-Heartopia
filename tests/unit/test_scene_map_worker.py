"""The map reader never blocks Tk or publishes an old process's geometry."""
from unittest.mock import Mock
from utils.ui.scene_map_worker import SceneMapWorker


def test_reader_is_lazy_and_closed_when_the_game_changes():
    reader = Mock()
    reader.snapshot.return_value = {'state': 'ready', 'objects': [], 'colliders': []}
    factory = Mock(return_value=reader)
    worker = SceneMapWorker('profile', factory=factory)
    worker.poll_once()
    factory.assert_not_called()
    worker.set_pid(77)
    worker.poll_once()
    assert worker.take_latest()['state'] == 'ready'
    worker.set_pid(None)
    worker.poll_once()
    reader.close.assert_called_once()
    worker.close()


def test_late_snapshot_of_old_process_is_discarded():
    reader = Mock()
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    def sample():
        worker.set_pid(88)
        return {'state': 'ready', 'objects': [{'position': [1, 2, 3]}], 'colliders': []}
    reader.snapshot.side_effect = sample
    worker.set_pid(77)
    worker.poll_once()
    assert worker.take_latest() is None
    worker.close()


def test_scene_failure_does_not_break_coordinate_reader_or_expose_addresses():
    reader = Mock()
    reader.snapshot.side_effect = ValueError('ReadProcessMemory 0x1234')
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(77)
    worker.poll_once()
    result = worker.take_latest()
    assert result['state'] == 'error' and result['objects'] == result['colliders'] == []
    assert '0x' not in result['message'] and result['diagnostic']
    reader.close.assert_called_once()
    worker.close()


def test_navigation_snapshot_is_independent_of_ui_queue_and_expires():
    now = [0.]
    reader = Mock()
    reader.snapshot.return_value = {'state': 'ready', 'objects': [], 'colliders': []}
    worker = SceneMapWorker('profile', factory=lambda *a: reader, clock=lambda: now[0])
    worker.set_pid(77)
    worker.poll_once()
    ui_scene = worker.take_latest()
    assert worker.take_latest() is None
    assert worker.navigation_snapshot(77) is ui_scene
    assert worker.navigation_snapshot(88) is None
    now[0] = 6.1
    assert worker.navigation_snapshot(77) is None
    worker.poll_once()
    assert worker.navigation_snapshot(77) is ui_scene
    worker.set_pid(88)
    assert worker.navigation_snapshot(77) is worker.navigation_snapshot(88) is None
    worker.close()


def test_scene_failure_immediately_invalidates_navigation_geometry():
    reader = Mock()
    reader.snapshot.return_value = {'state': 'ready', 'objects': [], 'colliders': []}
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(77)
    worker.poll_once()
    assert worker.navigation_snapshot(77)
    reader.snapshot.side_effect = ValueError('scene changed')
    worker.poll_once()
    assert worker.navigation_snapshot(77) is None
    worker.close()


def test_navigation_waits_for_native_world_reload_instead_of_using_partial_obstacles():
    reader = Mock()
    reader.snapshot.return_value = {'state': 'ready', 'world_state': 'unavailable',
                                    'objects': [], 'colliders': []}
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(77)
    worker.poll_once()
    assert worker.take_latest()['state'] == 'ready'  # Other map layers remain visible.
    assert worker.navigation_snapshot(77) is None
    reader.snapshot.return_value = {'state': 'ready', 'world_state': 'ready',
                                    'objects': [], 'colliders': []}
    worker.poll_once()
    assert worker.navigation_snapshot(77) is reader.snapshot.return_value
    worker.close()
