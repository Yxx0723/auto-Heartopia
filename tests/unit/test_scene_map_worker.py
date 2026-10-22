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
    reader.close.assert_not_called()
    worker.poll_once(); worker.poll_once()
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


def test_one_torn_scene_read_retries_coherently_without_rebuilding_reader():
    reader = Mock()
    good = {'state': 'ready', 'world_state': 'ready', 'objects': [], 'colliders': []}
    reader.snapshot.side_effect = [ValueError('场景列表在读取时发生变化'), good]
    factory = Mock(return_value=reader)
    worker = SceneMapWorker('profile', factory=factory)
    worker.set_pid(77); worker.poll_once()
    assert worker.take_latest() is good and worker.navigation_snapshot(77) is good
    factory.assert_called_once(); reader.close.assert_not_called()
    worker.close()


def test_brief_read_failure_retains_only_stale_fixed_display_not_navigation_or_animals():
    now = [0.]
    good = {'state': 'ready', 'world_state': 'ready', 'position': [0., 0., 0.], 'cache_context': (77, 1),
            'objects': [{'category': 'tree', 'position': [1., 0., 0.], 'map_static': True,
                         'map_id': (1,), 'resource_available': 3},
                        {'category': 'bird', 'position': [2., 0., 0.]}], 'colliders': [],
            'global_map_resources': [{'category': 'fish'}]}
    reader = Mock(); reader.snapshot.return_value = good
    worker = SceneMapWorker('profile', factory=lambda *args: reader, clock=lambda: now[0])
    worker.set_pid(77); worker.poll_once(); worker.take_latest()
    reader.snapshot.side_effect = ValueError('scene changed'); now[0] = 2.
    worker.poll_once(); display = worker.take_latest()
    assert display['state'] == 'updating' and display['display_stale']
    assert [o['category'] for o in display['objects']] == ['tree']
    assert display['objects'][0]['map_cached'] and 'resource_available' not in display['objects'][0]
    assert not display['global_map_resources'] and worker.navigation_snapshot(77) is None
    now[0] = 7.; worker.poll_once()
    assert worker.take_latest()['objects'] == []
    worker.set_pid(88); worker.poll_once()
    assert worker.take_latest()['objects'] == []
    worker.close()


def test_native_world_failure_retains_display_and_records_its_actual_reason(tmp_path):
    now = [0.]
    good = {'state': 'ready', 'world_state': 'ready', 'position': [0.,0.,0.], 'cache_context': (77,1),
            'objects': [{'category': 'tree', 'map_static': True, 'map_id': (1,), 'position': [0.,0.,1.]}],
            'colliders': []}
    reader = Mock(); reader.snapshot.return_value = good
    worker = SceneMapWorker('profile', factory=lambda *args: reader, clock=lambda: now[0],
                            diagnostics_path=tmp_path/'scene.jsonl')
    worker.set_pid(77); worker.poll_once(); worker.take_latest()
    reader.snapshot.return_value = {**good, 'world_state': 'unavailable',
                                    'world_diagnostic': '碰撞集合在读取时发生变化'}
    now[0] = 2.; worker.poll_once()
    assert worker.take_latest()['display_stale'] and worker.navigation_snapshot(77) is None
    worker.close()
    assert '碰撞集合在读取时发生变化' in (tmp_path/'scene.jsonl').read_text(encoding='utf-8')
