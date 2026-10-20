"""Keep errors after the UI closes without growing or breaking playback."""
import json
from unittest.mock import Mock

from utils.ui.playback_diagnostics import PlaybackDiagnostics
from utils.ui.point_playback import PointPlayback


def test_error_status_persists_in_a_small_rotating_history(tmp_path):
    path = tmp_path / 'playback.jsonl'
    log = PlaybackDiagnostics(path)
    log.handler.maxBytes = 220
    for index in range(20):
        log.write({'state': 'playing', 'message': 'moving', 'waypoint': index,
                   'navigation_path': [[0., 0., 1.]] * 100})
    log.write({'state': 'error', 'message': 'input failed', 'waypoint': 3})
    log.close()
    lines = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert lines[-1]['state'] == 'error' and lines[-1]['waypoint'] == 3
    assert 'navigation_path' not in lines[-1]
    assert len(list(tmp_path.glob('playback.jsonl*'))) <= 3


def test_unwritable_diagnostic_path_does_not_break_status_or_close(tmp_path):
    blocker = tmp_path / 'blocked'
    blocker.write_text('not a directory', encoding='utf-8')
    player = PointPlayback(tmp_path / 'profile.json', diagnostics_path=blocker / 'playback.jsonl')
    player.update('error', 'input failed')
    assert player.take_latest()['state'] == 'error'
    player.close()


def test_write_failure_cannot_hide_playback_status(tmp_path):
    player = PointPlayback(tmp_path / 'profile.json', diagnostics_path=tmp_path / 'playback.jsonl')
    player.diagnostics.handler.handle = Mock(side_effect=OSError('disk full'))
    player.update('stopped', 'keys released')
    assert player.take_latest()['message'] == 'keys released'
    player.close()
