"""Bounded, live-checked trials to a forward point of an observed corridor."""
from collections import Counter
from utils.core.coordinate_route import distance
from utils.core.memory_corridor import SHORTCUT_MIN_GAIN, SHORTCUT_MIN_METERS, SHORTCUT_CHECK_SECONDS
from utils.core.collection_schedule import FAILED_ENTRY_SECONDS

# Engineering search bounds, not measured terrain/character capability limits.
LOCAL_RADIUS=12.
LOCAL_ARC=32.
LOCAL_POINTS=64
LOCAL_HEIGHT=.35


class LocalMemoryShortcut:
    def __init__(self):
        self.aim=None
        self.key=None
        self.checked_at=float('-inf')
        self.rejected={}
        self.diagnostics={}

    def fail(self,now):
        if self.key is not None:
            self.rejected[self.key]=now+FAILED_ENTRY_SECONDS
            if len(self.rejected)>64:self.rejected.pop(next(iter(self.rejected)))
        self.aim=self.key=None

    def choose(self,position,path,scope,waypoint,obstacles,fresh,now,check,failed_entry):
        context=(scope,waypoint)
        reasons=Counter()
        def reason(point):
            check()
            if abs(position[1]-point[1])>LOCAL_HEIGHT:return 'height'
            if not obstacles.covers_search(position,distance(position,point)+1.):return 'coverage'
            a=(position[0],position[2]);b=(point[0],point[2])
            if not obstacles.clear(a,b,escape=True):return 'collision'
            if obstacles.risk_cost(a,b)>0 or failed_entry(a,b,position[1]):return 'direction_risk'
            return None
        if not fresh:
            self.aim=self.key=None
            self.diagnostics={'state':'rejected','reasons':{'snapshot_not_fresh':1}}
            return None
        if self.aim is not None:
            if self.key[:2]!=context or self.aim not in path:
                self.aim=self.key=None
                self.diagnostics={'state':'inactive','success_observed':False}
            else:
                issue=reason(self.aim)
                if issue is None:return self.aim
                reasons[issue]+=1
                self.fail(now)
        if now-self.checked_at<SHORTCUT_CHECK_SECONDS:
            if reasons:self.diagnostics={'state':'rejected','reasons':dict(reasons)}
            return None
        self.checked_at=now
        arc=0.;previous=position;best=None
        for index,point in enumerate(path[:LOCAL_POINTS]):
            check()
            arc+=distance(previous,point);previous=point
            if arc>LOCAL_ARC:break
            if abs(position[1]-point[1])>LOCAL_HEIGHT:
                reasons['height']+=1;break
            direct=distance(position,point);gain=arc-direct
            if direct>LOCAL_RADIUS or direct<.3 or gain<SHORTCUT_MIN_METERS or direct>arc*(1-SHORTCUT_MIN_GAIN):
                reasons['insufficient_local_gain']+=1;continue
            key=(*context,tuple(point))
            if now<self.rejected.get(key,float('-inf')):
                reasons['cooldown']+=1;continue
            issue=reason(point)
            if issue:
                reasons[issue]+=1;continue
            if best is None or gain>=best[0]:best=(gain,index,tuple(point),arc,direct,key)
        if best is None:
            self.diagnostics={'state':'rejected','reasons':dict(reasons)}
            return None
        gain,index,self.aim,arc,direct,self.key=best
        self.diagnostics={'state':'trial','target':self.aim,'forward_index':index,
                          'recorded_distance':arc,'trial_distance':direct,'saving':gain,
                          'success_observed':False}
        return self.aim
