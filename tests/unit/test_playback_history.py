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
