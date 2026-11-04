"""Saved destinations over the proven local controller, without game guidance."""
from utils.core.guidance_navigation import GuidanceNavigator
from utils.core.traversal_evidence import scene_scope
from utils.core.coordinate_route import distance
from utils.core.collection_schedule import MEMORY_REJOIN_SECONDS, FAILED_ENTRY_SECONDS
from utils.core.coordinate_navigation import point_segment_distance
from utils.core.memory_corridor import (EXECUTION_CHORD_ERROR, SHORTCUT_MIN_GAIN,
    SHORTCUT_MIN_METERS, SHORTCUT_CHECK_SECONDS)
from utils.core.scene_read import navigation_quality_valid
from utils.core import thresholds as policy


class CollectionNavigator(GuidanceNavigator):
    continuous_collection = True
    continuous_guidance = False

    @property
    def hybrid_collection(self):
        return not self.native_map_collection

    def __init__(self,*args,native_map_collection=False,**kwargs):
        if type(native_map_collection) is not bool:
            raise ValueError('采集原生引导开关无效')
        self.native_map_collection=native_map_collection
        self.native_scene_provider=None
        self.memory_retry_at=0.
        self.memory_scope=None
        self.short_trial=None
        self.short_rejected={}
        self.short_check_at=float('-inf')
        super().__init__(*args,respect_water=True,**kwargs)

    def blocked(self,position,target,**kwargs):
        if self.short_trial is not None:
            self.short_rejected[self.short_trial]=self.clock()+FAILED_ENTRY_SECONDS
            if len(self.short_rejected)>64:self.short_rejected.pop(next(iter(self.short_rejected)))
            self.short_trial=None
            if self.path_memory:self.path_memory.selected=None
        elif self.path_memory:
            self.path_memory.reject_selected(position=position)
        self.memory_retry_at=self.clock()+MEMORY_REJOIN_SECONDS
        return super().blocked(position,target,**kwargs)

    def _shore_target(self,position,target,check):
        # A saved collection destination is not a disposable guidance lookahead.
        # Preserve its exact arrival and interaction semantics, including water.
        return None

    def _fresh_collision(self):
        return isinstance((self.scene or {}).get('quality'),dict) \
            and scene_scope(self.scene) is not None \
            and navigation_quality_valid(self.scene,self.scene_clock())

    def _observe_saved_walk(self,position,target):
        if self.path_memory is None or self.waypoint is None:
            return
        leg=(self.waypoint,tuple(target))
        if self.memory_leg!=leg:
            self.memory_leg=leg
            self.learning_started=self.learning_time
            self.learning_finished=None
            self.memory_loaded=self.memory_failed=False
            self.memory_path,self.walked_path,self.walked_times=[],[],[]
            self.memory_aim=None
        self._observe_walk(position)

    def _direct_trial(self,position,target,key,check,before_plan):
        now=self.clock()
        if now<self.short_rejected.get(key,float('-inf')) or not self._fresh_collision() \
                or abs(position[1]-target[1])>.35 \
                or not self.obstacles.covers_search(position,distance(position,target)+1.):
            return None
        a=(position[0],position[2]);b=(target[0],target[2])
        if not self.obstacles.clear(a,b,escape=True) or self.obstacles.risk_cost(a,b)>0 \
                or self.risks.repeats_failed_entry(a,b,position[1]):
            return None
        check()
        self._observe_saved_walk(position,target)
        if self.short_trial!=key:
            self.path=[tuple(target)];self.goal=tuple(target)
        self.short_trial=key
        self.memory_state='learning'
        result=self._next_target(position,target,check,before_plan)
        self.last_decision={**self.last_decision,'kind':'checked_shortcut_trial',
                            'requested_target':tuple(target),'success_observed':False}
        return result

    def _memory_target(self,position):
        # Keep historical turns unless a fresh collision permit confirms the
        # slightly wider jitter envelope. No geometry is rewritten here.
        if not self._fresh_collision():
            return super()._memory_target(position)
        start=(position[0],position[2]);selected=self.memory_path[0]
        for index,point in enumerate(self.memory_path):
            if distance(position,point)>policy.COORD_NAV_MEMORY_LOOKAHEAD or abs(position[1]-point[1])>.35:
                break
            end=(point[0],point[2])
            if any(point_segment_distance((p[0],p[2]),start,end)>EXECUTION_CHORD_ERROR
                   for p in self.memory_path[:index]):
                continue
            if not self.obstacles.clear(start,end,escape=True) or self.obstacles.risk_cost(start,end)>0:
                break
            selected=point
        return selected

    def _advance_memory_progress(self,position):
        if not self.memory_path or self.memory_aim not in self.memory_path or not self._fresh_collision():
            return
        limit=min(self.memory_path.index(self.memory_aim),len(self.memory_path)-2)
        current=(position[0],position[2])
        for index in range(limit,-1,-1):
            a,b=self.memory_path[index:index+2]
            dx,dz=b[0]-a[0],b[2]-a[2];length=dx*dx+dz*dz
            if length<1e-9 or abs(position[1]-a[1])>.35:
                continue
            part=((position[0]-a[0])*dx+(position[2]-a[2])*dz)/length
            if 0<=part<=1 and point_segment_distance(current,(a[0],a[2]),(b[0],b[2]))<=EXECUTION_CHORD_ERROR \
                    and self.obstacles.clear(current,(b[0],b[2]),escape=True) \
                    and self.obstacles.risk_cost(current,(b[0],b[2]))==0:
                del self.memory_path[:index+1]
                self.memory_aim=None
                return

    def next_target(self,position,target,check=lambda:None,before_plan=lambda:None):
        if self.last_decision.get('kind') in ('observed_partial_memory','checked_shortcut_trial'):
            self.last_decision={}
        library=getattr(self,'traversal_memory',None)
        scope=scene_scope(self.scene_provider())
        if scope is not None and scope!=self.memory_scope:
            self.memory_scope=scope
            if self.path_memory:
                self.path_memory.blocked_entries.clear()
                self.memory_loaded=False
                self.memory_path=[]
                self.memory_aim=None
                self.memory_leg=None
        if self.path_memory and self.memory_loaded and not self.memory_path \
                and not self.memory_failed and self.clock()>=self.memory_retry_at:
            self.memory_loaded=False
            self.memory_retry_at=self.clock()+MEMORY_REJOIN_SECONDS
        key=(self.waypoint,tuple(target),scope)
        if self.short_trial is not None:
            self._refresh_obstacles(position,target)
            if self.short_trial==key:
                trial=self._direct_trial(position,target,key,check,before_plan)
                if trial is not None:return trial
            self.short_trial=None
            self.memory_loaded=False;self.memory_path=[];self.memory_aim=None
            self.invalidate()
        if self.memory_path and self.memory_aim is not None:
            self._refresh_obstacles(position,target)
            self._advance_memory_progress(position)
            if self.memory_path:
                self.memory_aim=self._memory_target(position)
        # Complete destination-specific corridors keep their existing priority.
        if library is None:
            planned=super().next_target(position,target,check,before_plan)
            return self._compare_memory(position,target,key,planned,check,before_plan)
        planned=None
        if self.path_memory is not None and self.path_memory.legs.get(str(self.waypoint)):
            planned=super().next_target(position,target,check,before_plan)
            if self.memory_state=='reusing':
                return self._compare_memory(position,target,key,planned,check,before_plan)
        self._refresh_obstacles(position,target)
        direction=[b-a for a,b in zip(position,target)]
        candidates=library.candidates(position,scene_scope(self.scene),direction)
        choices=[]
        for row in candidates:
            check()
            a,b=row['start'],row['end']
            entry=distance(position,a)
            aim=a if entry>.4 else b
            if distance(position,aim)<.15 or distance(b,target)>=distance(position,target)-.05:
                continue
            p=(position[0],position[2]);q=(aim[0],aim[2])
            if not self.obstacles.clear(p,q,escape=True) or self.obstacles.risk_cost(p,q)>0:
                continue
            choices.append((distance(b,target)+entry+10*row.get('failures',0),aim))
        if choices:
            aim=min(choices,key=lambda v:v[0])[1]
            # Join only exactly contiguous observed edges. Short sampling edges
            # must not turn continuous walking back into 0.1-second point pulses.
            following={}
            for row in candidates:
                following.setdefault(tuple(row['start']),[]).append(row['end'])
            chain=[tuple(aim)]
            for _ in range(16):
                options=following.get(tuple(aim),())
                if not options:
                    break
                end=min(options,key=lambda p:distance(p,target))
                if tuple(end) in chain or distance(position,end)>3. or distance(end,target)>=distance(aim,target):
                    break
                a=(position[0],position[2]);b=(end[0],end[2])
                if any(point_segment_distance((p[0],p[2]),a,b)>.35 for p in chain) \
                        or not self.obstacles.clear(a,b,escape=True) or self.obstacles.risk_cost(a,b)>0:
                    break
                aim=end;chain.append(tuple(end))
            if distance(position,aim)<1. and distance(position,target)>1.:
                return planned if planned is not None else super().next_target(position,target,check,before_plan)
            self._observe_saved_walk(position,target)
            result=self._next_target(position,aim,check,before_plan,approach_goal=target)
            self.last_decision={**self.last_decision,'kind':'observed_partial_memory',
                                'requested_target':tuple(target),'local_target':tuple(aim)}
            return result
        return planned if planned is not None else super().next_target(position,target,check,before_plan)

    def _compare_memory(self,position,target,key,planned,check,before_plan):
        if self.memory_state!='reusing' or not self.memory_path or self.clock()<self.short_check_at:
            return planned
        self.short_check_at=self.clock()+SHORTCUT_CHECK_SECONDS
        old=distance(position,self.memory_path[0])+sum(distance(a,b) for a,b in zip(self.memory_path,self.memory_path[1:]))
        direct=distance(position,target)
        if old-direct<SHORTCUT_MIN_METERS or direct>old*(1-SHORTCUT_MIN_GAIN):
            return planned
        trial=self._direct_trial(position,target,key,check,before_plan)
        return trial if trial is not None else planned
