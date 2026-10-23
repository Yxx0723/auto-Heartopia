"""Manual point capture must survive restarts and never destroy prior points."""
import json
import msvcrt

import pytest

from utils.core.coordinate_monitor_view import MonitorView
from utils.core.coordinate_points import append_point, empty_points, ordered_waypoints, choose_start_index
from utils.drivers.coordinate_point_store import PointStore


STAMP = '2026-10-11T22:30:00+08:00'
SNAPSHOT = {'position': [-45.5, 23.25, 128.2], 'sample_at': STAMP}


def live_view():
    view = MonitorView()
    view.accept({'state': 'connected', 'session': 'a', 'sequence': 1,
                 'sample_age': .2, **SNAPSHOT}, 10.)
    return view


def test_append_preserves_precision_and_does_not_mutate_input():
    original = empty_points()
    first = append_point(original, SNAPSHOT, STAMP)
    second = append_point(first, {**SNAPSHOT, 'position': [0., 0., 0.]}, STAMP)
    assert original['points'] == []
    assert len(first['points']) == 1
    assert [p['id'] for p in second['points']] == [1, 2]
    assert second['points'][0] == {'id': 1, 'name': '点位 1', **SNAPSHOT, 'saved_at': STAMP}
    assert second['points'][1]['position'] == [0., 0., 0.]


@pytest.mark.parametrize('position', [[float('nan'), 1, 2], [True, 1, 2], [1, 2], [100000, 1, 2]])
def test_invalid_positions_are_rejected(position):
    with pytest.raises(ValueError):
        append_point(empty_points(), {**SNAPSHOT, 'position': position}, STAMP)


@pytest.mark.parametrize('stamp', [None, '', 'yesterday', '2026-10-11'])
def test_sample_time_must_be_valid_and_include_timezone(stamp):
    with pytest.raises(ValueError):
        append_point(empty_points(), {**SNAPSHOT, 'sample_at': stamp}, STAMP)


def test_capture_uses_fresh_live_snapshot_independent_of_reference_and_trail():
    view = live_view()
    view.set_reference()
    view.clear_trail()
    snapshot = view.point_snapshot(10.1)
    assert snapshot == SNAPSHOT
    snapshot['position'][0] = 99
    assert view.position[0] == -45.5


@pytest.mark.parametrize('mode', ['paused', 'disconnected', 'expired', 'old_sample'])
def test_capture_rejects_paused_lost_and_stale_readings(mode):
    view = live_view()
    now = 10.1
    if mode == 'paused':
        view.set_paused(True)
    elif mode == 'disconnected':
        view.accept({'state': 'disconnected'}, now)
    elif mode == 'expired':
        now = 12.
    else:
        # Reader sample age and time spent in the UI add up to staleness.
        now = 11.4
    with pytest.raises(ValueError):
        view.point_snapshot(now)


def test_store_reloads_and_two_instances_append_without_losing_points(tmp_path):
    path = tmp_path / '点位' / '我的点位.json'
    a, b = PointStore(path), PointStore(path)
    assert a.load()['points'] == []
    assert a.append(SNAPSHOT, STAMP)['points'][0]['id'] == 1
    assert b.append(SNAPSHOT, STAMP)['points'][-1]['id'] == 2
    assert len(PointStore(path).load()['points']) == 2
    assert json.loads(path.read_text(encoding='utf-8'))['points'][0]['name'] == '点位 1'


@pytest.mark.parametrize('content', ['broken json', '{"schema": 99, "points": []}',
                                     '{"schema": "coordinate-points-v1", "points": [{}]}'])
def test_corrupt_existing_file_is_never_overwritten(tmp_path, content):
    path = tmp_path / '我的点位.json'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError):
        PointStore(path).append(SNAPSHOT, STAMP)
    assert path.read_text(encoding='utf-8') == content


def test_failed_replace_preserves_file_and_next_save_recovers(tmp_path, monkeypatch):
    path = tmp_path / '我的点位.json'
    store = PointStore(path)
    store.append(SNAPSHOT, STAMP)
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        def denied(*args):
            raise PermissionError('test write failure')
        patch.setattr('utils.drivers.coordinate_point_store.os.replace', denied)
        with pytest.raises(PermissionError):
            store.append(SNAPSHOT, STAMP)
    assert path.read_bytes() == before
    assert len(store.load()['points']) == 1
    assert not list(tmp_path.glob('*.tmp'))
    assert len(store.append(SNAPSHOT, STAMP)['points']) == 2


def test_busy_store_does_not_overwrite_and_lock_is_released(tmp_path):
    store = PointStore(tmp_path / '点位.json')
    store.append(SNAPSHOT, STAMP)
    before = store.path.read_bytes()
    with store.path.with_suffix('.lock').open('r+b') as lock:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            with pytest.raises(OSError, match='另一个窗口'):
                store.append(SNAPSHOT, STAMP)
            assert store.path.read_bytes() == before
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    assert len(store.append(SNAPSHOT, STAMP)['points']) == 2


def test_playback_preserves_every_saved_point_including_close_and_duplicate_points():
    data = empty_points()
    positions = [[0., 0., 0.], [.1, 0., 0.], [.1, 0., 0.], [-2., 1., 0.]]
    for position in positions:
        data = append_point(data, {**SNAPSHOT, 'position': position}, STAMP)
    points = ordered_waypoints(data)
    assert [p['position'] for p in points] == positions
    assert [p['id'] for p in points] == [1, 2, 3, 4]
    points[0]['position'][0] = 99
    assert data['points'][0]['position'][0] == 0


def test_playback_rejects_empty_point_file():
    with pytest.raises(ValueError, match='点位'):
        ordered_waypoints(empty_points())


def test_start_mode_preserves_full_route_or_selects_nearest_point_with_height():
    points = [{'position': [0, 0, 0]}, {'position': [4, 0, 0]}, {'position': [4, 10, 0]}]
    assert choose_start_index(points, [4, 9, 0], 'first') == 0
    assert choose_start_index(points, [4, 9, 0], 'nearest') == 2
    assert choose_start_index(points, [4, 1, 0], 'nearest') == 1
    assert choose_start_index(points, [2, 0, 0], 'nearest') == 0


@pytest.mark.parametrize('points,position,mode', [([], [0, 0, 0], 'nearest'),
                                                 ([{'position': [0, 0, 0]}], [float('nan'), 0, 0], 'nearest'),
                                                 ([{'position': [0, 0, 0]}], [0, 0, 0], 'unknown')])
def test_invalid_start_selection_is_rejected(points, position, mode):
    with pytest.raises(ValueError):
        choose_start_index(points, position, mode)


@pytest.mark.parametrize('seconds', [0, .5, 3, 300])
def test_collection_wait_is_saved_with_new_points_and_copied_to_playback(seconds):
    data = append_point(empty_points(), SNAPSHOT, STAMP, collect_wait=seconds)
    assert data['points'][0]['collect_wait'] == seconds
    assert ordered_waypoints(data)[0]['collect_wait'] == seconds


@pytest.mark.parametrize('seconds', [-1, 301, float('nan'), float('inf'), True, '3'])
def test_invalid_collection_wait_is_rejected_without_changing_points(seconds):
    from utils.core.coordinate_points import set_collect_wait, validate_collect_wait
    data = append_point(empty_points(), SNAPSHOT, STAMP)
    before = json.dumps(data)
    with pytest.raises(ValueError):
        validate_collect_wait(seconds)
    with pytest.raises(ValueError):
        set_collect_wait(data, 1, seconds)
    data['points'][0]['collect_wait'] = seconds
    with pytest.raises(ValueError):
        ordered_waypoints(data)
    del data['points'][0]['collect_wait']
    assert json.dumps(data) == before


def test_per_point_wait_updates_only_selected_metadata_and_preserves_old_format():
    from utils.core.coordinate_points import set_collect_wait
    data = append_point(append_point(empty_points(), SNAPSHOT, STAMP), SNAPSHOT, STAMP)
    before = json.dumps(data)
    updated = set_collect_wait(data, 2, 4.5)
    assert json.dumps(data) == before
    assert updated['points'][0] == data['points'][0]
    assert updated['points'][1] == {**data['points'][1], 'collect_wait': 4.5}
    all_updated = set_collect_wait(updated, None, 0)
    assert [p['collect_wait'] for p in all_updated['points']] == [0, 0]
    assert 'collect_wait' not in ordered_waypoints(data)[0]


@pytest.mark.parametrize('point_id', [0, 3, True, '1'])
def test_unknown_collection_point_cannot_update_file(point_id):
    from utils.core.coordinate_points import set_collect_wait
    with pytest.raises(ValueError):
        set_collect_wait(append_point(empty_points(), SNAPSHOT, STAMP), point_id, 3)


def test_wait_settings_survive_reopen_without_losing_another_instances_new_point(tmp_path):
    path = tmp_path / '点位.json'
    first, other = PointStore(path), PointStore(path)
    first.append(SNAPSHOT, STAMP, collect_wait=2)
    other.append(SNAPSHOT, STAMP)
    result = first.set_collect_wait(1, 4.5)
    assert len(result['points']) == 2
    assert [p.get('collect_wait') for p in PointStore(path).load()['points']] == [4.5, None]


def test_wait_write_failure_preserves_the_original_file(tmp_path, monkeypatch):
    store = PointStore(tmp_path / '点位.json')
    store.append(SNAPSHOT, STAMP)
    before = store.path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr('utils.drivers.coordinate_point_store.os.replace',
                      lambda *args: (_ for _ in ()).throw(PermissionError('wait save failed')))
        with pytest.raises(PermissionError):
            store.set_collect_wait(1, 5)
    assert store.path.read_bytes() == before
    assert store.set_collect_wait(1, 2)['points'][0]['collect_wait'] == 2


def test_collection_count_survives_waypoint_conversion():
    data = append_point(empty_points(), SNAPSHOT, STAMP)
    data['points'][0]['collect_count'] = 3
    assert ordered_waypoints(data)[0]['collect_count'] == 3


@pytest.mark.parametrize('count', [0, -1, True, 1.5, 101, '3'])
def test_invalid_collection_count_is_rejected(count):
    data = append_point(empty_points(), SNAPSHOT, STAMP)
    data['points'][0]['collect_count'] = count
    with pytest.raises(ValueError):
        ordered_waypoints(data)
