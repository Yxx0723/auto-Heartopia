"""Offline directed-cost suggestions. Never starts playback or reads a live game."""
import hashlib
import math
from statistics import median


def positive(value):
    return type(value) in (int,float) and math.isfinite(value) and value>0


def costs_from_memory(route, memory):
    from utils.drivers.route_path_memory import RoutePathMemory
    import json
    points=route['points'];targets=[p['position'] for p in points]
    fingerprint=hashlib.sha256(json.dumps(targets,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    if memory.get('schema')!='route-path-memory-v1' or memory.get('route_fingerprint')!=fingerprint:
        raise ValueError('路线记忆与原路线坐标不匹配')
    rows=[]
    for key,variants in memory.get('legs',{}).items():
        end=int(key)
        if not 0<=end<len(points):continue
        for trace in variants:
            if not isinstance(trace,list) or len(trace)<2:continue
            starts=[p['id'] for p in points if math.dist(p['position'],trace[0])<=.5]
            if len(starts)!=1 or math.dist(trace[-1],targets[end])>1.:continue
            if any(math.dist(a,b)>6. for a,b in zip(trace,trace[1:])):continue
            record=memory.get('statistics',{}).get(RoutePathMemory._identity(trace),{})
            seconds=[v for v in record.get('recent_seconds',[]) if positive(v)]
            if not seconds or record.get('movement_evidence')!='continuous_coordinates_with_walk_input':continue
            # Legacy records carry no durable scene identity: useful, but exploratory.
            rows.append({'from':starts[0],'to':end+1,'seconds':seconds,'level':'L2'})
    return rows


def suggest(route, rows, whitelist, closed_loop=True, max_trials=2000):
    from utils.core.coordinate_points import validate_points
    validate_points(route)
    points=route['points'];count=len(points)
    if count>200:raise ValueError('离线首版最多支持200个点')
    if any(type(i)is not int or not 1<=i<=count for i in whitelist):raise ValueError('白名单无效')
    grouped={}
    for row in rows:
        a,b=row.get('from'),row.get('to')
        if type(a)is not int or type(b)is not int or not 1<=a<=count or not 1<=b<=count or a==b:
            raise ValueError('有向成本点位编号无效')
        samples=row.get('seconds')
        if not isinstance(samples,list) or not samples or not all(positive(v) for v in samples):
            raise ValueError('实走耗时必须是有限正数')
        level=row.get('level')
        if level not in ('L1','L2'):raise ValueError('成本来源等级无效；未知边应缺省')
        # L1 requires explicit scene and completed-walk provenance.
        if level=='L1' and (row.get('scene_verified') is not True or row.get('walk_completed') is not True):
            raise ValueError('L1 缺少场景与实走完成证据')
        grouped.setdefault((a,b),[]).append((level,samples))
    costs={}
    for edge,entries in grouped.items():
        level='L1' if any(l=='L1' for l,_ in entries) else 'L2'
        samples=[v for l,values in entries if l==level for v in values]
        costs[edge]={'seconds':median(samples),'level':level,'samples':len(samples)}
    duplicates={tuple(p['position']) for p in points if sum(q['position']==p['position'] for q in points)>1}
    locked={p['id'] for p in points if p['id']==1 or p['id'] not in whitelist or 'special_resource' in p or tuple(p['position']) in duplicates}
    def edges(order):
        pairs=list(zip(order,order[1:]))
        if closed_loop and len(order)>1:pairs.append((order[-1],order[0]))
        return pairs
    def score(order):
        pairs=edges(order)
        return (sum(e not in costs for e in pairs),sum(costs[e]['seconds'] for e in pairs if e in costs))
    original=list(range(1,count+1));order=original[:];trials=0
    while trials<max_trials:
        best=order;best_score=score(order)
        for i in range(count):
            for j in range(i+1,count):
                if order[i] in locked or order[j] in locked or any(i<anchor-1<j for anchor in locked):continue
                if trials>=max_trials:break
                trials+=1
                candidate=order[:];candidate[i],candidate[j]=candidate[j],candidate[i]
                value=score(candidate)
                if value<best_score:best,best_score=candidate,value
        if best==order:break
        order=best
    def details(order):
        unknown=[list(e) for e in edges(order) if e not in costs]
        known=[dict(source=a,target=b,**costs[(a,b)]) for a,b in edges(order) if (a,b) in costs]
        return {'unknown_edges':unknown,'known_edges':known,
            'known_seconds_subtotal':sum(e['seconds'] for e in known),
            'estimated_total_seconds':None if unknown else sum(e['seconds'] for e in known)}
    baseline,candidate=details(original),details(order)
    enough=(not baseline['unknown_edges'] and not candidate['unknown_edges'] and
        all(e['level']=='L1' and e['samples']>=3 for e in baseline['known_edges']+candidate['known_edges']))
    improved=order!=original and score(order)<score(original)
    status=('recommended' if enough and improved and score(order)[1]<score(original)[1]*.95
            else 'rejected' if enough else 'exploratory')
    return {'schema':'offline-route-reorder-v1','status':status,'order':order,'original_order':original,
        'whitelist':sorted(whitelist),'anchors':sorted(locked),'closed_loop':closed_loop,
        'trials':trials,'changed':order!=original,'baseline':baseline,'candidate':candidate,
        'reason':('measured_improvement' if status=='recommended' else
            'insufficient_verified_directed_costs' if not enough else 'no_material_improvement'),
        'memory_migration':False,'runtime_sorting':False}
