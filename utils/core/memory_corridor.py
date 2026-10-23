"""Bounded corridor comparison and observed active-time profiles, not geometry synthesis."""
import math
from statistics import median
from utils.core import thresholds as policy


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def recent_times(record):
    values = record.get('recent_seconds', [])
    return [float(v) for v in values[-policy.COORD_MEMORY_RECENT_WINDOW:] if positive(v)] if isinstance(values,list) else []


def typical_seconds(record, fallback):
    times = recent_times(record)
    return median(times) if times else (record['best_seconds'] if positive(record.get('best_seconds')) else fallback)


def reliability(record, pending=False):
    outcomes = record.get('recent_outcomes')
    if isinstance(outcomes,list) and outcomes and all(type(v) is int and v in (0,1) for v in outcomes):
        outcomes = outcomes[-policy.COORD_MEMORY_RECENT_WINDOW:]
        if pending:outcomes = (outcomes+[0])[-policy.COORD_MEMORY_RECENT_WINDOW:]
        rate = outcomes.count(0)/len(outcomes)
    else:
        successes, failures = record.get('successes',1), record.get('blocked',0)
        successes = successes if type(successes) is int and 0 <= successes <= 10**9 else 1
        failures = failures if type(failures) is int and 0 <= failures <= 10**9 else 0
        failures += int(pending)
        rate = failures/max(1,successes+failures)
    return 1+policy.COORD_MEMORY_FAILURE_WEIGHT*rate


def append_outcome(record, success):
    old = record.get('recent_outcomes', [])
    if not isinstance(old,list) or any(type(v) is not int or v not in (0,1) for v in old):old=[]
    record['recent_outcomes'] = (old+[int(success)])[-policy.COORD_MEMORY_RECENT_WINDOW:]


def resample(path):
    lengths=[0.]
    for a,b in zip(path,path[1:]):lengths.append(lengths[-1]+math.dist(a,b))
    if lengths[-1] <= 0:return [path[0]]*policy.COORD_MEMORY_COMPARE_SAMPLES
    result=[];i=0
    for n in range(policy.COORD_MEMORY_COMPARE_SAMPLES):
        at=lengths[-1]*n/(policy.COORD_MEMORY_COMPARE_SAMPLES-1)
        while i<len(path)-2 and lengths[i+1]<at:i+=1
        span=lengths[i+1]-lengths[i]
        part=(at-lengths[i])/span if span else 0.
        result.append(tuple(a+(b-a)*part for a,b in zip(path[i],path[i+1])))
    return result


def similar(first, second):
    # Comparison points are never saved as a route or used as a shortcut.
    a,b=resample(first),resample(second)
    return all(math.dist(p,q)<=policy.COORD_MEMORY_CORRIDOR_DISTANCE
               and abs(p[1]-q[1])<=policy.COORD_MEMORY_CORRIDOR_HEIGHT for p,q in zip(a,b))


def valid_profile(times, size, duration):
    return (isinstance(times,(list,tuple)) and len(times)==size and size>=2
            and all(type(t) in (int,float) and math.isfinite(t) for t in times)
            and all(a<=b for a,b in zip(times,times[1:]))
            and abs(times[0])<=1e-6 and positive(duration)
            and abs(times[-1]-duration)<=max(.05,duration*.01))
