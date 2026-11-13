from copy import deepcopy
from datetime import datetime
import json
from unittest.mock import Mock

import pytest

from utils.drivers.blueberry_survey import BlueberrySurvey
from utils.drivers.coordinate_point_store import PointStore
from utils.ui.scene_map_worker import SceneMapWorker


def bush(position, category='blueberry'):
    return dict(category=category, position=position, map_static=True)


def scene(*objects, version='v1'):
    return dict(survey_scene='startown', survey_version=version, objects=list(objects), colliders=[])


def test_survey_only_records_blueberries_and_keeps_heights_and_restarts(tmp_path):
    s = BlueberrySurvey(tmp_path)
    s.observe(scene(bush([0, 0, 0]), bush([0, 3, 0]), bush([5, 0, 5], 'raspberry'),
                    bush([6, 0, 6], 'bush'), bush([float('nan'), 0, 0])))
    s = BlueberrySurvey(tmp_path)
    s.observe(scene(bush([.02, 0, 0]), bush([0, 3, 0])))
    assert [row['position'] for row in s.load()] == [[0, 0, 0], [0, 3, 0]]
    with pytest.raises(ValueError):
        s.observe({**scene(), 'survey_scene': 'other'})
    assert len(s.load()) == 2


def test_route_only_accepts_currently_verified_stands_and_preserves_backup(tmp_path, monkeypatch):
    obstacle = Mock()
    obstacle.clear.return_value = True
    monkeypatch.setattr('utils.core.coordinate_navigation.ObstacleMap', Mock(return_value=obstacle))
    s = BlueberrySurvey(tmp_path)
    snap = scene(bush([2, 0, 0]), bush([4, 0, 0]))
    s.observe(snap)
    store = PointStore(tmp_path/'蓝莓.json')
    at = datetime.now().astimezone().isoformat()
    first = dict(id=1, name='保留', position=[0, 0, 0], sample_at=at, saved_at=at, collect_wait=0)
    old = dict(schema='coordinate-points-v1', points=[first], extra='保留')
    store.create(old)
    raw = store.path.read_bytes()
    assert s.preview(old, snap) == ([], 0, 2)
    with pytest.raises(ValueError):
        s.verify_stand(snap, [2, 3, 0], [2, 0, 0])
    s.verify_stand(snap, [2.5, 0, 0], [2, 0, 0])
    obstacle.clear.return_value = False
    assert s.preview(old, snap) == ([], 0, 2)
    obstacle.clear.return_value = True
    assert s.preview(old, scene(bush([4, 0, 0]))) == ([], 0, 2)
    adds, _, _ = s.preview(old, snap)
    assert len(adds) == 1
    import utils.drivers.blueberry_survey as module
    with monkeypatch.context() as patch:
        patch.setattr(module, 'atomic_write_json', Mock(side_effect=OSError('denied')))
        with pytest.raises(OSError):
            s.update_route(store, old, adds)
    assert store.path.read_bytes() == raw
    assert all(p.read_bytes() == raw for p in (tmp_path/'路线回收站').glob('*.json'))
    backup = s.update_route(store, old, adds)
    assert backup.read_bytes() == raw
    result = store.load()
    assert result['points'][0] == first and result['points'][1]['id'] == 2
    assert result['extra'] == old['extra']
    assert s.preview(result, snap) == ([], 1, 1)
    s.observe(scene(bush([2, 0, 0]), version='v2'))
    assert not s.verified(s.load()[0], 'v2')


def test_survey_uses_raw_current_pid_and_collision_quality():
    worker = SceneMapWorker('profile', clock=lambda: 10.)
    worker.set_pid(7)
    raw = dict(scene(), state='ready', world_state='ready', quality={
        'navigation_eligible': True, 'root_verified_at': 9.,
        'parts': {p: dict(state='complete', observed_from=8., observed_to=9.)
                  for p in ('entities', 'managed_collisions', 'native_collisions')}})
    worker.latest_scene = raw
    assert worker.survey_snapshot(7) is raw
    assert worker.survey_snapshot(8) is None
    stale = deepcopy(raw)
    stale['quality']['parts']['entities']['observed_from'] = 1.
    worker.latest_scene = stale
    assert worker.survey_snapshot(7) is None
    raw['quality']['parts']['managed_collisions']['state'] = 'partial'
    worker.latest_scene = raw
    assert worker.survey_snapshot(7) is None


def test_published_blueberry_seed_is_a_valid_ten_point_route():
    from pathlib import Path
    from utils.core.coordinate_points import validate_points
    path = Path(__file__).resolve().parents[2]/'data'/'routes'/'蓝莓.json'
    assert len(validate_points(json.loads(path.read_text(encoding='utf-8-sig')))['points']) == 10
