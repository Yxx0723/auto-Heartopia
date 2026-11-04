"""Only observed directed movement, never planned lines, enters reusable memory."""
import json
from unittest.mock import Mock
import pytest
from utils.drivers.playback_history import PlaybackHistory


def test_failed_run_keeps_successful_partial_motion_and_all_archive_sources(tmp_path):
    h=PlaybackHistory(tmp_path,{},'test',{})
    scope='scene-a'
    h.record_motion([0,0,0],[0,0,1],.3,scope)
    h.event({'state':'playing','navigation_path':[[0,0,1],[0,0,90]]})
    h.event({'state':'error','message':'blocked'})
    h.close()
    other=PlaybackHistory(tmp_path,{},'test',{})
    rows=other.memory.candidates([0,0,0],scope)
    assert len(rows)==1 and rows[0]['end']==[0,0,1]
    assert other.memory.candidates([0,0,0],'scene-b')==[]
    assert json.loads((h.path/'learning.json').read_text())['memory_database']=='../寻路记忆库.sqlite3'
    other.close()


def test_unobserved_plan_is_never_a_reusable_edge(tmp_path):
    h=PlaybackHistory(tmp_path,{},'test',{})
    h.event({'state':'playing','navigation_path':[[0,0,0],[0,0,5]]})
    assert h.memory.candidates([0,0,0],'scene-a')==[]
    h.close()


def test_motion_rejects_jump_missing_scope_discontinuity_and_wrong_layer(tmp_path):
    h=PlaybackHistory(tmp_path,{},'test',{})
    for a,b,dt,scope,mode in [([0,0,0],[0,0,1],.3,None,'walk'),
                              ([0,0,0],[0,0,1],.3,'a','jump'),
                              ([0,0,0],[0,0,50],.1,'a','walk'),
                              ([0,0,0],[0,0,1],4.,'a','walk')]:
        h.record_motion(a,b,dt,scope,mode)
    h.record_motion([0,0,0],[0,0,1],.3,'a')
    assert len(h.memory.candidates([0,0,0],'a'))==1
    assert not h.memory.candidates([0,4,0],'a')
    assert not h.memory.candidates([0,0,1],'a',direction=[0,0,-1])
    h.close()


def test_blocked_library_does_not_disable_raw_history(tmp_path):
    (tmp_path/'寻路记忆库.sqlite3').mkdir()
    h=PlaybackHistory(tmp_path,{},'test',{})
    h.record_motion([0,0,0],[0,0,1],.3,'a')
    h.event({'state':'error','message':'test'})
    h.close()
    assert not h.error and (h.path/'events.jsonl').read_text()
    assert h.memory.error


@pytest.mark.parametrize('bad', ['release','jump','scene','expiry','collision','lease'])
def test_controller_requires_unbroken_input_fresh_scene_and_clear_motion(bad):
    from utils.flows.coordinate_player import _Follower
    p=_Follower.__new__(_Follower)
    p.reader=Mock();p.keys={'w'};p._memory_anchor=None;p.direction_unsafe_until=-1
    p.navigator=Mock();p.navigator.scene={'state':'ready','cache_context':'a'}
    p.navigator.scene_provider.return_value=p.navigator.scene
    p.navigator.collision_deadline.return_value=100
    p.navigator.motion_clear.return_value=True
    p._record_memory_motion((0,0,0),0)
    if bad=='release':
        p.device=Mock();p.release()
    elif bad=='jump':p.keys={'w','space'}
    elif bad=='scene':p.navigator.scene_provider.return_value={'state':'ready','cache_context':'b'}
    elif bad=='expiry':p.navigator.collision_deadline.return_value=.2
    elif bad=='collision':p.navigator.motion_clear.return_value=False
    else:
        from threading import RLock
        p.input_lease=Mock(lock=RLock(),tripped=True,error=None,deadline=100.)
    p._record_memory_motion((0,0,1),.3)
    p.reader.record_motion.assert_not_called()


def test_controller_positive_motion_is_sent_to_memory():
    from utils.flows.coordinate_player import _Follower
    p=_Follower.__new__(_Follower)
    p.reader=Mock();p.keys={'w'};p._memory_anchor=None;p.direction_unsafe_until=-1
    p.navigator=Mock();p.navigator.scene={'state':'ready','cache_context':'a'}
    p.navigator.scene_provider.return_value=p.navigator.scene
    p.navigator.collision_deadline.return_value=100;p.navigator.motion_clear.return_value=True
    p._record_memory_motion((0,0,0),0);p._record_memory_motion((0,0,1),.3)
    assert p.reader.record_motion.call_count==1


def test_collection_reuses_partial_edge_but_rechecks_real_wall(tmp_path):
    from utils.core.collection_navigation import CollectionNavigator
    from utils.core.traversal_evidence import scene_scope
    from tests.unit.test_coordinate_navigation import scene, box
    from utils.core.coordinate_navigation import NavigationWait
    snapshot=dict(scene(),cache_context='a')
    h=PlaybackHistory(tmp_path,{},'test',{})
    h.record_motion([0,0,0],[0,0,1],.3,scene_scope(snapshot))
    nav=CollectionNavigator(lambda:snapshot,water_areas=(),clock=lambda:0)
    nav.traversal_memory=h.memory
    nav.next_target((0,0,0),(0,0,6))
    assert nav.last_decision['kind']=='observed_partial_memory'
    snapshot['colliders']=[box(-2,2,.3,.7)]
    nav.invalidate()
    try:nav.next_target((0,0,0),(0,0,6))
    except NavigationWait:
        # Ordinary planner may reject the route; it must not use the blocked edge.
        assert nav.last_decision.get('kind')!='observed_partial_memory'
    else:assert nav.last_decision.get('kind')!='observed_partial_memory'
    h.close()


def test_stall_penalty_is_directed_temporary_and_never_promotes_a_plan(tmp_path,monkeypatch):
    h=PlaybackHistory(tmp_path,{},'test',{})
    h.record_motion([0,0,0],[0,0,1],.3,'a')
    h.record_motion([0,0,1],[0,0,0],.3,'a')
    h.event({'state':'recovering','incident_kind':'movement_stall','traversal_scope':'a','traversal_mode':'walk',
             'position':[0,0,0],'target':[0,0,9],'position_age_seconds':0.})
    rows=h.memory.candidates([0,0,0],'a')
    assert next(r for r in rows if r['end']==[0,0,1])['failures']>0
    assert next(r for r in rows if r['end']==[0,0,0])['failures']==0
    import utils.drivers.traversal_memory as module
    now=module.time.time();monkeypatch.setattr(module.time,'time',lambda:now+121)
    assert all(r['failures']==0 for r in h.memory.candidates([0,0,0],'a'))
    h.close()


def test_playback_semantic_read_is_archived_and_attachment_rejection_is_preserved(tmp_path):
    from utils.drivers.playback_history import RecordingPosition
    h=PlaybackHistory(tmp_path,{},'test',{})
    reader=Mock();reader.read_for_playback.return_value=(0,0,1)
    wrapper=RecordingPosition(reader,h,lambda:1.)
    assert wrapper.read_for_playback()==(0,0,1)
    assert h.summary['sample_count']==1
    reader.read_for_playback.side_effect=ValueError('attached')
    with pytest.raises(ValueError,match='attached'):wrapper.read_for_playback()
    assert h.summary['sample_count']==1
    h.close()


def test_storage_is_bounded_and_deduplicated(tmp_path,monkeypatch):
    import utils.drivers.traversal_memory as module
    monkeypatch.setattr(module,'EDGE_LIMIT',2)
    h=PlaybackHistory(tmp_path,{},'test',{})
    for i in range(4):
        h.record_motion([0,0,i],[0,0,i+1],.3,'a')
    h.record_motion([0,0,3],[0,0,4],.3,'a')
    h.close()
    other=PlaybackHistory(tmp_path,{},'test',{})
    assert len(other.memory.candidates([0,0,2],'a'))==2
    other.close()


def test_unknown_database_version_is_not_overwritten(tmp_path):
    import sqlite3
    db=sqlite3.connect(tmp_path/'寻路记忆库.sqlite3');db.execute('PRAGMA user_version=99');db.close()
    h=PlaybackHistory(tmp_path,{},'test',{})
    assert h.memory.error and h.memory.db is None
    h.close()
    db=sqlite3.connect(tmp_path/'寻路记忆库.sqlite3')
    assert db.execute('PRAGMA user_version').fetchone()[0]==99
    db.close()
