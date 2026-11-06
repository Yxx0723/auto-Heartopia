import json
from utils.core.playback_comparison import compare


def archive(root,name,seconds,**updates):
    path=root/name;path.mkdir()
    data={'duration_seconds':seconds,'options':{'start_mode':'first','loop_count':1},
        'state':'finished','cycle_finished_count':1,'reached_count':6,'waypoint_count':6,'skipped_count':0}
    data.update(updates)
    (path/'summary.json').write_text(json.dumps(data),encoding='utf8')
    (path/'route.json').write_text('{}')
    (path/'events.jsonl').write_text('')
    (path/'trajectory.jsonl').write_text(json.dumps({'position':[0,0,0]}))
    return str(path)


def test_requires_three_pairs_and_explicit_no_manual_movement(tmp_path):
    pairs=[{'baseline':archive(tmp_path,'b'+str(i),100),'candidate':archive(tmp_path,'c'+str(i),90),
            'manual_intervention':False} for i in range(3)]
    assert compare(pairs[:2])['status']=='insufficient_pairs'
    assert compare(pairs)['status']=='improved'
    pairs[0].pop('manual_intervention')
    assert compare(pairs)['valid_pairs']==2


def test_incomplete_or_changed_routes_cannot_be_declared_faster(tmp_path):
    pairs=[{'baseline':archive(tmp_path,'b'+str(i),100),'candidate':archive(tmp_path,'c'+str(i),10,state='stopped'),
            'manual_intervention':False} for i in range(3)]
    assert compare(pairs)['status']=='regression'
    assert all('incomplete_run' in p['reasons'] for p in compare(pairs)['pairs'])


def test_more_safety_events_fails_even_when_candidate_is_faster(tmp_path):
    pairs=[{'baseline':archive(tmp_path,'b'+str(i),100),'candidate':archive(tmp_path,'c'+str(i),90),
            'manual_intervention':False} for i in range(3)]
    from pathlib import Path
    (Path(pairs[0]['candidate'])/'events.jsonl').write_text(json.dumps({'navigation_wait_reason':'scene'}))
    assert compare(pairs)['status']=='regression'
