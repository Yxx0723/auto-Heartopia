import json
import sqlite3

import pytest

from utils.core.traversal_evidence import learning_scope
from utils.drivers.playback_history import PlaybackHistory
from utils.drivers.route_archive_maintenance import backup_database, check_archive, import_archive
from utils.drivers.traversal_memory import TraversalMemory


def test_archive_exports_verified_motion_and_imports_only_once(tmp_path):
    original = tmp_path/'original'
    history = PlaybackHistory(original, {}, '测试路线', {'auto_navigate': True})
    history.event({'state':'playing','navigation_path':[[0,0,50]]})
    history.record_motion([0,0,0],[0,0,1],.3,'map',recovery=True)
    history.event({'state':'stopped'})
    history.close()
    route, summary, run_id, state = check_archive(history.path)
    assert state == 'verified'
    assert summary['program_version'] != 'unknown'
    assert summary['verified_motion_count'] == 1
    assert json.loads((history.path/'motion.jsonl').read_text(encoding='utf-8'))['recovery']
    database = tmp_path/'imported.sqlite3'
    assert import_archive(history.path, route, summary, run_id, database) == (1,0)
    assert import_archive(history.path, route, summary, run_id, database) == (0,0)
    memory = TraversalMemory(database, 'test')
    try:
        rows = memory.candidates([0,0,0],'map')
        assert len(rows) == 1 and rows[0]['end'] == [0,0,1]
        assert rows[0]['confidence'] == 1
    finally:
        memory.close()


def test_open_archive_and_corrupted_archive_are_distinguishable(tmp_path):
    history = PlaybackHistory(tmp_path, {}, 'test', {})
    assert check_archive(history.path)[-1] == 'interrupted'
    history.close()
    (history.path/'trajectory.jsonl').write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError):
        check_archive(history.path)


def test_sqlite_backup_captures_live_wal_and_recovers_corrupt_primary(tmp_path):
    database = tmp_path/'memory.sqlite3'
    memory = TraversalMemory(database, 'a')
    memory.observe([0,0,0],[0,0,1],.3,'map')
    memory.flush(tmp_path,'finished',force=True)
    assert backup_database(database) == '备份已验证'
    memory.close()
    database.write_bytes(b'damaged')
    assert backup_database(database) == '已从校验通过的备份恢复'
    with sqlite3.connect(database) as restored:
        assert restored.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert restored.execute('SELECT count(*) FROM edges').fetchone() == (1,)
    assert next(tmp_path.glob('memory.damaged-*.sqlite3')).read_bytes() == b'damaged'


def test_main_map_identity_is_versioned_and_other_scenes_stay_runtime_scoped():
    main = dict(state='ready',cache_context=(1,'a'),terrain_revision='verified',
                map_scene_name='startown',traversal_map_version='a'*64)
    assert learning_scope(main) == learning_scope(dict(main,cache_context=(2,'b')))
    assert learning_scope(main) != learning_scope(dict(main,traversal_map_version='b'*64))
    house = dict(main,map_scene_name='house')
    assert learning_scope(house) != learning_scope(dict(house,cache_context=(2,'b')))


def test_historical_penalty_is_soft_directed_and_lowered_by_verified_pass(tmp_path):
    database = tmp_path/'memory.sqlite3'
    first = TraversalMemory(database, 'failed')
    row = dict(kind='movement_stall',physical_stall=True,input_keys=['w'],
               position_source='last_successful_read',position_age_seconds=.1,
               traversal_mode='walk',learning_scope='map',position=[0,0,0],
               steering_target=[0,0,2])
    first.failed(row)
    first.flush(tmp_path,'stopped',force=True)
    assert first.historical_failures([0,0,0],'map',10)
    assert not first.historical_failures([0,3,0],'map',10)
    first.close()
    for run_id in ('success1','success2'):
        memory = TraversalMemory(database,run_id)
        memory.observe([0,0,0],[0,0,1],.3,'map')
        memory.flush(tmp_path,'finished',force=True)
        assert not memory.historical_failures([0,0,0],'map',10)
        memory.close()
    reader = TraversalMemory(database,'check')
    try:
        assert reader.candidates([0,0,0],'map')[0]['confidence'] == 2
        assert reader.db.execute('SELECT count(*) FROM failures').fetchone() == (1,)
    finally:
        reader.close()
