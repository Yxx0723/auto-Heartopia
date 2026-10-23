"""Each run keeps its own durable trace, including stopped and failed runs."""
import json
from datetime import datetime, timezone
from utils.drivers.playback_history import PlaybackHistory, RecordingPosition


def test_two_runs_never_overwrite_and_keep_route_snapshot_and_progress(tmp_path):
    now = lambda: datetime(2026, 10, 20, tzinfo=timezone.utc)
    for state in ('finished', 'stopped'):
        record = PlaybackHistory(tmp_path, {'points': [{'position': [1, 2, 3]}]},
                                 '橘子', {'loop_count': 2}, now)
        record.event({'state': 'playing', 'cycle': 1, 'waypoint': 1})
        record.sample([1, 2, 3], 10.)
        record.sample([4, 2, 7], 11.)
        record.event({'state': 'reached', 'cycle': 1, 'waypoint': 1})
        record.event({'state': state, 'message': 'done'})
        record.close()
    folders = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(folders) == 2
    for folder in folders:
        summary = json.loads((folder/'summary.json').read_text(encoding='utf-8'))
        assert summary['route_name'] == '橘子'
        assert summary['sample_count'] == 2 and summary['distance_xz'] == 5.
        assert summary['reached_count'] == 1
        assert summary['state'] in ('finished', 'stopped')
        assert json.loads((folder/'route.json').read_text(encoding='utf-8'))['points']
        rows = [json.loads(s) for s in (folder/'trajectory.jsonl').read_text(encoding='utf-8').splitlines()]
        assert rows[1]['elapsed_seconds'] == 1. and rows[1]['waypoint'] == 1


def test_reader_passthrough_and_invalid_samples_and_incomplete_run(tmp_path):
    class Reader:
        value = [1., 2., 3.]
        def read(self): return self.value
        def close(self): self.closed = True
        def tool(self): return 'tool'
    history = PlaybackHistory(tmp_path, {}, 'route', {})
    reader = Reader()
    proxy = RecordingPosition(reader, history, lambda: 5.)
    assert proxy.read() == reader.value and proxy.tool() == 'tool'
    reader.value = [float('nan'), 0, 0]
    proxy.read()
    proxy.close()
    assert reader.closed
    history.close()
    summary = json.loads((history.path/'summary.json').read_text(encoding='utf-8'))
    assert summary['sample_count'] == 1 and summary['state'] == 'interrupted'


def test_unwritable_history_does_not_break_input_release(tmp_path):
    blocked = tmp_path/'file'
    blocked.write_text('x')
    history = PlaybackHistory(blocked, {}, 'route', {})
    history.event({'state': 'error', 'message': 'original error'})
    history.sample([0, 0, 0], 0.)
    history.close()
    assert history.error


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_planned_paths_are_saved_separately_and_duplicate_frames_do_not_repeat(tmp_path):
    record = PlaybackHistory(tmp_path, {}, 'route', {})
    status = {'state': 'playing', 'cycle': 1, 'waypoint': 1, 'navigation_path': [[1, 0, 1], [2, 0, 2]]}
    record.event(status)
    record.event(status)
    record.event({**status, 'navigation_path': [[2, 0, 2], [3, 0, 3]]})
    record.close()
    plans = read_rows(record.path/'plans.jsonl')
    assert len(plans) == 2 and plans[0]['path'] == status['navigation_path']
    assert record.summary['planned_path_count'] == 2


def test_stall_keeps_position_direction_target_and_geometry_without_claiming_contact(tmp_path):
    route = {'points': [{'position': [9, 2, 3]}]}
    record = PlaybackHistory(tmp_path, route, 'route', {})
    record.event({'state': 'playing', 'cycle': 1, 'waypoint': 1})
    record.sample([1, 2, 3], 1.)
    record.event({'state': 'recovering', 'incident_kind': 'movement_stall', 'message': 'no progress',
                  'steering_target': [4, 2, 3], 'input_keys': ['w'],
                  'collision_context': {'source': 'navigation_scene', 'colliders': []}})
    record.event({'state': 'recovering', 'recovery_phase': 'jump'})
    record.close()
    incidents = read_rows(record.path/'incidents.jsonl')
    assert len(incidents) == 1
    row = incidents[0]
    assert row['position'] == [1, 2, 3] and row['position_source'] == 'last_recorded_sample'
    assert row['target'] == [9, 2, 3] and row['waypoint'] == 1
    assert row['contact_confirmed'] is False
    assert row['input_keys'] == ['w'] and row['steering_target'] == [4, 2, 3]
    assert record.summary['movement_stall_count'] == 1


def test_identical_navigation_wait_is_one_episode_and_reentry_is_separate(tmp_path):
    record = PlaybackHistory(tmp_path, {}, 'route', {})
    status = {'state': 'navigating', 'navigation_wait_reason': 'scene', 'waypoint': 1}
    record.event(status)
    record.event(status)
    record.event({'state': 'playing', 'waypoint': 1})
    record.event(status)
    record.close()
    rows = read_rows(record.path/'incidents.jsonl')
    assert len(rows) == 2 and all(row['kind'] == 'navigation_wait' for row in rows)
    assert all(row['position'] is None for row in rows)


def test_unknown_position_is_not_replaced_with_target_or_origin(tmp_path):
    record = PlaybackHistory(tmp_path, {'points': [{'position': [8, 9, 10]}]}, 'route', {})
    record.event({'state': 'error', 'message': 'unavailable', 'waypoint': 1})
    record.close()
    row = read_rows(record.path/'incidents.jsonl')[0]
    assert row['position'] is None and row['target'] == [8, 9, 10]


def test_plan_serialization_failure_never_raises_into_playback(tmp_path):
    record = PlaybackHistory(tmp_path, {}, 'route', {})
    record.event({'state': 'playing', 'navigation_path': [[float('nan'), 0, 0]]})
    record.close()
    assert not record.error
    assert read_rows(record.path/'plans.jsonl') == []


def test_learning_manifest_links_observations_without_promoting_them(tmp_path):
    record = PlaybackHistory(tmp_path, {'points':[{'position':[1,2,3]}]}, '稀有木材', {})
    record.event({'state':'playing', 'waypoint':1})
    record.sample([1,2,3], 1.)
    record.event({'state':'partial', 'skipped_count':1})
    record.close()
    manifest = json.loads((record.path/'learning.json').read_text(encoding='utf-8'))
    assert manifest['schema'] == 'playback-learning-index-v1'
    assert manifest['route_name'] == '稀有木材'
    assert manifest['state'] == 'partial' and manifest['run_complete'] is False
    assert manifest['automatic_promotion'] is False
    assert manifest['collision_contact_confirmed'] is False
    assert manifest['files']['actual_trajectory'] == 'trajectory.jsonl'
    assert manifest['files']['collision_candidates'] == 'incidents.jsonl'
    assert len(manifest['route_sha256']) == 64


def test_manifest_failure_does_not_stop_raw_recording(tmp_path):
    record = PlaybackHistory(tmp_path, {}, 'route', {})
    original = record._json
    def write(name, data):
        if name == 'learning.json':
            raise OSError('index unavailable')
        return original(name, data)
    record._json = write
    record.event({'state':'playing'})
    record.sample([0,0,0], 1.)
    record.event({'state':'finished'})
    record.close()
    assert not record.error and record.archive_error
    assert len(read_rows(record.path/'trajectory.jsonl')) == 1
    assert json.loads((record.path/'summary.json').read_text(encoding='utf-8'))['state'] == 'finished'


def test_archive_route_name_cannot_escape_root_and_waiting_is_not_complete(tmp_path):
    record = PlaybackHistory(tmp_path, {}, '../../bad/name:*?', {})
    assert record.path.parent == tmp_path
    manifest = json.loads((record.path/'learning.json').read_text(encoding='utf-8'))
    assert manifest['finalized'] is False and manifest['run_complete'] is False
    record.close()
    final = json.loads((record.path/'learning.json').read_text(encoding='utf-8'))
    assert final['finalized'] is True and final['state'] == 'interrupted'
    assert final['run_complete'] is False


def test_finished_manifest_identifies_exact_route_snapshot_without_modifying_it(tmp_path):
    import hashlib
    route = {'points':[{'position':[1,2,3], 'collect_count':3}]}
    record = PlaybackHistory(tmp_path, route, '稀有木材', {})
    record.event({'state':'finished'})
    record.close()
    raw = (record.path/'route.json').read_bytes()
    manifest = json.loads((record.path/'learning.json').read_text(encoding='utf-8'))
    assert manifest['route_sha256'] == hashlib.sha256(raw).hexdigest()
    assert manifest['run_complete'] is True
    assert json.loads(raw) == route
