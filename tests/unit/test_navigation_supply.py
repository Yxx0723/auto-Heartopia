"""Supply is elapsed usable time, never a count of successful scans."""
from unittest.mock import Mock

import pytest

from utils.drivers.mono_scene import SceneMetadata
from utils.ui.scene_map_worker import SceneMapWorker


def ready(start, end, native_start=None):
    return {'state': 'ready', 'world_state': 'ready', 'navigation_only': True,
            'objects': [], 'colliders': [], 'quality': {
                'navigation_eligible': True, 'root_verified_at': end,
                'parts': {key: {'state': 'complete', 'observed_from': value, 'observed_to': end}
                          for key, value in [('managed_collisions', start),
                                             ('native_collisions', start if native_start is None else native_start)]}}}


def test_object_header_batches_type_and_domain_without_caching_object_identity():
    cells = {0x10000: 0x20000, 0x20000: 0x40000, 0x20010: 0x30000}
    def read(at, size):
        return bytes((cells.get((at+i)//8*8, 0) >> (((at+i) % 8)*8)) & 255 for i in range(size))
    memory = Mock(side_effect=read)
    meta = SceneMetadata(memory, 0x30000)
    meta.batch_object_headers = True
    meta.cls = Mock(return_value={'address': 0x40000})
    assert meta.obj(0x10000)['address'] == 0x40000
    assert memory.call_count == 2  # Old implementation makes three remote reads.
    cells[0x20010] = 0x50000
    with pytest.raises(ValueError, match='归属'):
        meta.obj(0x10000)


@pytest.mark.parametrize('duration,expected_gap', [(2., 0.), (3., .05), (4., 2.05), (5., 4.05)])
def test_serial_scan_reports_gap_not_just_publish_success(duration, expected_gap):
    from utils.core.navigation_supply import SnapshotSupply
    supply = SnapshotSupply()
    supply.publish(ready(0., duration), duration, duration)
    supply.start(duration)  # Preparation is separately measured; demand starts now.
    next_at = 2*duration+.05
    supply.publish(ready(duration+.05, next_at), next_at, duration)
    metrics = supply.metrics(next_at)
    assert metrics['snapshot_gap_seconds'] == pytest.approx(expected_gap)
    assert metrics['snapshot_usable_ratio'] == pytest.approx(1-expected_gap/(duration+.05))
    assert metrics['snapshot_overlap_margin'] == pytest.approx(6-next_at)


def test_expired_on_publish_and_error_are_counted_as_unusable_time():
    from utils.core.navigation_supply import SnapshotSupply
    supply = SnapshotSupply(); supply.start(0.)
    supply.publish(ready(0., 6.5), 6.5, 6.5)
    assert supply.metrics(7.)['snapshot_usable_ratio'] == 0.
    supply.publish(ready(7., 8.), 8., 1.)
    supply.publish({'state': 'error'}, 9., 1.)
    metrics = supply.metrics(10.)
    assert metrics['snapshot_usable_seconds'] == pytest.approx(1.)
    assert metrics['snapshot_longest_gap_seconds'] == pytest.approx(8.)
    assert metrics['snapshot_current_gap_seconds'] == pytest.approx(1.)


def test_expiry_uses_oldest_required_source_and_rejects_partial_or_future():
    from utils.core.navigation_supply import navigation_expiry
    assert navigation_expiry(ready(1., 3., native_start=2.), 3.) == 7.
    scene = ready(1., 3.); scene['quality']['parts']['native_collisions']['state'] = 'partial'
    assert navigation_expiry(scene, 3.) is None
    assert navigation_expiry(ready(1., 4.), 3.) is None


def test_navigation_only_publication_does_not_build_display_history():
    reader = Mock(); reader.snapshot.return_value = ready(0., 1.)
    worker = SceneMapWorker('profile', factory=lambda *a: reader, clock=lambda: 1.)
    worker.set_pid(7); worker.history.update = Mock(side_effect=AssertionError('display work'))
    worker.poll_once()
    from utils.core.prepared_navigation import PreparedNavigationScene
    published = worker.navigation_snapshot(7)
    assert isinstance(published, PreparedNavigationScene)
    assert published['quality'] == reader.snapshot.return_value['quality']
    assert tuple(published['colliders']) == tuple(reader.snapshot.return_value['colliders'])
    worker.close()


def test_worker_metrics_start_on_demand_and_reset_on_pid_switch():
    now = [1.]; reader = Mock(); reader.snapshot.return_value = ready(0., 1.)
    worker = SceneMapWorker('profile', factory=lambda *a: reader, clock=lambda: now[0])
    worker.set_pid(7); worker.poll_once(); now[0] = 2.
    worker.navigation_snapshot(7); now[0] = 8.
    metrics = worker.navigation_metrics(7)
    assert metrics['snapshot_usable_seconds'] == pytest.approx(4.)
    assert metrics['snapshot_longest_gap_seconds'] == pytest.approx(2.)
    worker.set_pid(8)
    assert worker.navigation_metrics(7) is None
    assert worker.navigation_metrics(8)['snapshot_demand_seconds'] == 0.
    worker.close()


def test_real_reader_reports_completed_phase_timings_without_renewing_observation():
    from tests.unit.test_scene_read_recovery import minimal_scene_reader
    reader = minimal_scene_reader(); scene = reader.snapshot()
    phases = reader.last_read_stats['phases']
    assert {'registry', 'entities', 'barriers', 'managed_collisions', 'native_collisions',
            'optional_definitions', 'final_validation'} <= phases.keys()
    assert all(value >= 0. for value in phases.values())
    assert scene['quality']['parts']['managed_collisions']['observed_from'] \
        <= scene['quality']['parts']['native_collisions']['observed_from']


def test_user_pause_is_excluded_but_runtime_outage_is_not():
    from utils.core.navigation_supply import SnapshotSupply
    supply = SnapshotSupply(); supply.publish(ready(0., 1.), 1., 1.)
    supply.start(1.); supply.pause(3.)
    supply.metrics(100.)
    supply.start(100.)
    metrics = supply.metrics(103.)
    assert metrics['snapshot_demand_seconds'] == 5.
    assert metrics['snapshot_usable_seconds'] == 2.
    assert metrics['snapshot_current_gap_seconds'] == 3.


@pytest.mark.parametrize('part', [None, 123, {}, {'observed_from': float('nan')}])
def test_malformed_part_cannot_break_supply_logging(part):
    from utils.core.navigation_supply import SnapshotSupply
    supply = SnapshotSupply(); scene = ready(0., 1.)
    scene['quality']['parts']['managed_collisions'] = part
    supply.publish(scene, 1., 1.)
    assert supply.metrics(1.)['snapshot_remaining_ttl'] is None


def test_scan_percentiles_have_bounded_window_and_explicit_count():
    from utils.core.navigation_supply import SnapshotSupply
    supply = SnapshotSupply()
    for i in range(1000): supply.publish({'state':'error'}, float(i), float(i))
    metrics = supply.metrics(1000.)
    assert metrics['scan_count'] == 1000 and metrics['scan_window_count'] == 256
    assert metrics['scan_duration_max'] == 999.


def test_object_header_bulk_read_preserves_invalid_pointer_and_short_read_guards():
    from utils.core.scene_read import SceneReadIssue
    meta = SceneMetadata(Mock(side_effect=[(0x20000).to_bytes(8, 'little'),
        (3).to_bytes(8, 'little') + bytes(8) + (0x30000).to_bytes(8, 'little')]), 0x30000)
    meta.batch_object_headers = True
    with pytest.raises(SceneReadIssue): meta.obj(0x10000)
    meta.read = Mock(side_effect=[(0x20000).to_bytes(8, 'little'), bytes(16)])
    with pytest.raises(ValueError, match='变化'): meta.obj(0x10000)


def test_default_metadata_keeps_legacy_read_path():
    meta = SceneMetadata(Mock(side_effect=[(0x20000).to_bytes(8,'little'),
        (0x30000).to_bytes(8,'little'), (0x40000).to_bytes(8,'little')]), 0x30000)
    meta.cls = Mock(return_value={'address':0x40000})
    assert meta.obj(0x10000)['address'] == 0x40000
    assert meta.read.call_count == 3


def test_batched_header_retains_invalid_domain_pointer_error_category():
    from utils.core.scene_read import SceneReadIssue
    meta=SceneMetadata(Mock(side_effect=[(0x20000).to_bytes(8,'little'),
        (0x40000).to_bytes(8,'little')+bytes(8)+(3).to_bytes(8,'little')]),0x30000)
    meta.batch_object_headers=True
    with pytest.raises(SceneReadIssue) as caught:meta.obj(0x10000)
    assert caught.value.kind=='invalid_reference'


def test_query_and_metrics_do_not_renew_snapshot_deadline():
    from utils.core.navigation_supply import SnapshotSupply
    supply = SnapshotSupply(); supply.publish(ready(0., 1.), 1., 1.); supply.start(1.)
    for i in range(20): supply.metrics(1.+i*.3)
    metrics = supply.metrics(8.)
    assert metrics['snapshot_usable_seconds'] == 5.
    assert metrics['snapshot_current_gap_seconds'] == 2.


def test_late_old_pid_publication_cannot_pollute_metrics():
    reader = Mock(); worker = SceneMapWorker('profile',factory=lambda *a:reader,clock=lambda:1.)
    worker.set_pid(7)
    def snapshot():
        worker.set_pid(8)
        return ready(0.,1.)
    reader.snapshot.side_effect = snapshot
    worker.poll_once()
    assert worker.navigation_metrics(8)['scan_count'] == 0
    worker.close()


def test_unusable_ready_publication_is_logged_even_inside_normal_diagnostic_interval(tmp_path):
    import json
    now=[1.]; reader=Mock(); reader.snapshot.return_value=ready(0.,1.)
    path=tmp_path/'supply.jsonl'
    worker=SceneMapWorker('profile',factory=lambda *a:reader,clock=lambda:now[0],diagnostics_path=path)
    worker.set_pid(7);worker.poll_once()
    now[0]=2.; value=ready(1.,2.)
    value['quality']['parts']['native_collisions']['state']='partial'
    reader.snapshot.return_value=value;worker.poll_once();worker.close()
    rows=[json.loads(row) for row in path.read_text(encoding='utf-8').splitlines()]
    assert len(rows)==2
    assert json.loads(rows[-1]['message'])['navigation_usable_now'] is False


def test_guidance_control_feedback_pauses_supply_metrics_without_game_input(monkeypatch):
    from utils.ui.point_playback import PointPlayback
    from tests.unit.test_point_playback import FakeWindow
    workers=[]
    class Worker:
        def __init__(self,*a,**kw): self.pauses=0; workers.append(self)
        def set_pid(self,pid): pass
        def start(self): pass
        def close(self): pass
        def pause_navigation_metrics(self): self.pauses+=1
        def navigation_metrics(self,pid): return {'test_window':True}
        def navigation_snapshot(self,pid): return ready(0.,1.)
    class Controls:
        def __init__(self,window,size,feedback,stop): self.feedback=feedback
        def start(self): pass
        def close(self): pass
    monkeypatch.setattr('utils.ui.point_playback.SceneMapWorker',Worker)
    def runner(size,reader,device,window,controls,report,**kw):
        controls.feedback.update('paused','manual pause')
        assert workers[0].pauses == 1
        return {'arrived':False,'reason':'test stop'}
    playback=PointPlayback('profile',window_factory=FakeWindow,controls_factory=Controls,
        device_factory=lambda w:Mock(),guidance_runner=runner,scene_provider=lambda p:None,
        guidance_scene_factory=Mock())
    playback.start_guidance(7);playback.thread.join(2)
    assert not playback.active and workers[0].pauses == 2
    assert playback.take_latest()['navigation_supply'] == {'test_window':True}
    playback.close()
