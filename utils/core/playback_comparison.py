"""Read-only paired acceptance over complete saved playback archives."""
import hashlib
import json
import math
from pathlib import Path
from statistics import median


def load_run(path):
    path=Path(path)
    summary=json.loads((path/'summary.json').read_text(encoding='utf-8-sig'))
    events=[json.loads(line) for line in (path/'events.jsonl').read_text(encoding='utf-8-sig').splitlines() if line]
    samples=[json.loads(line) for line in (path/'trajectory.jsonl').read_text(encoding='utf-8-sig').splitlines() if line]
    options=summary.get('options',{})
    duration=summary.get('duration_seconds')
    if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=0:raise ValueError('回放耗时无效')
    return {'archive':str(path.resolve()),'route_sha256':hashlib.sha256((path/'route.json').read_bytes()).hexdigest(),
        'options':options,'start':samples[0]['position'] if samples else None,'seconds':duration,
        'completed':summary.get('reached_count',0),'failed':summary.get('skipped_count',0),
        'stalls':summary.get('movement_stall_count',0),
        'safe_events':sum(e.get('state')=='paused' or e.get('navigation_wait_reason')=='scene'
            or e.get('incident_kind') in ('height_wait_timeout','collision_contact') for e in events),
        'complete':summary.get('state')=='finished' and summary.get('cycle_finished_count')==1
            and summary.get('reached_count')==summary.get('waypoint_count') and summary.get('skipped_count',0)==0,
        'paused':any(e.get('state')=='paused' for e in events)}


def compare(pairs):
    results=[]
    for row in pairs:
        baseline=load_run(row['baseline']);candidate=load_run(row['candidate'])
        reasons=[]
        if row.get('manual_intervention') is not False:reasons.append('manual_control_not_confirmed')
        if baseline['route_sha256']!=candidate['route_sha256']:reasons.append('route_mismatch')
        if baseline['options']!=candidate['options']:reasons.append('options_mismatch')
        for run in (baseline,candidate):
            if run['options'].get('start_mode')!='first' or run['options'].get('loop_count')!=1:reasons.append('requires_first_and_one_loop')
            if not run['complete']:reasons.append('incomplete_run')
            if run['paused']:reasons.append('manual_pause_or_focus_change')
        if baseline['start'] is None or candidate['start'] is None or math.dist(baseline['start'],candidate['start'])>1.:
            reasons.append('start_mismatch')
        results.append({'valid':not reasons,'reasons':sorted(set(reasons)),
            'baseline':baseline,'candidate':candidate})
    valid=[p for p in results if p['valid']]
    status='insufficient_pairs';before=after=None
    hard_regression=any(p['baseline']['complete'] and not any(reason in p['reasons'] for reason in
        ('manual_control_not_confirmed','route_mismatch','options_mismatch','start_mismatch','requires_first_and_one_loop'))
        and (not p['candidate']['complete'] or p['candidate']['failed']>p['baseline']['failed']
            or p['candidate']['completed']<p['baseline']['completed']
            or p['candidate']['safe_events']>p['baseline']['safe_events']) for p in results)
    if len(valid)>=3:
        before=median(p['baseline']['seconds'] for p in valid);after=median(p['candidate']['seconds'] for p in valid)
        worse=any(p['candidate']['failed']>p['baseline']['failed'] or
            p['candidate']['completed']<p['baseline']['completed'] or
            p['candidate']['safe_events']>p['baseline']['safe_events'] for p in valid)
        status='regression' if worse or after>before else 'improved' if after<before else 'no_change'
    if hard_regression:status='regression'
    return {'schema':'collection-paired-acceptance-v1','status':status,'valid_pairs':len(valid),
        'required_pairs':3,'baseline_median_seconds':before,'candidate_median_seconds':after,
        'pairs':results,'limitations':'同工具、资源刷新状态和无手动移动需要人工核对；事件数受日志记录方式影响。'}
