"""Saved destinations over the proven local controller, without game guidance."""
from utils.core.guidance_navigation import GuidanceNavigator
from utils.core.traversal_evidence import scene_scope
from utils.core.coordinate_route import distance
from utils.core.collection_schedule import MEMORY_REJOIN_SECONDS
from utils.core.coordinate_navigation import point_segment_distance


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
        super().__init__(*args,respect_water=True,**kwargs)

    def blocked(self,position,target,**kwargs):
        if self.path_memory:
            self.path_memory.reject_selected(position=position)
        self.memory_retry_at=self.clock()+MEMORY_REJOIN_SECONDS
        return super().blocked(position,target,**kwargs)

    def _shore_target(self,position,target,check):
        # A saved collection destination is not a disposable guidance lookahead.
        # Preserve its exact arrival and interaction semantics, including water.
        return None

    def next_target(self,position,target,check=lambda:None,before_plan=lambda:None):
        if self.last_decision.get('kind')=='observed_partial_memory':
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
        # Complete destination-specific corridors keep their existing priority.
        if library is None:
            return super().next_target(position,target,check,before_plan)
        planned=None
        if self.path_memory is not None and self.path_memory.legs.get(str(self.waypoint)):
            planned=super().next_target(position,target,check,before_plan)
            if self.memory_state=='reusing':
                return planned
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
            if self.path_memory is not None and self.waypoint is not None:
                leg=(self.waypoint,tuple(target))
                if self.memory_leg!=leg:
                    self.memory_leg=leg
                    self.learning_started=self.learning_time
                    self.learning_finished=None
                    self.memory_loaded=self.memory_failed=False
                    self.memory_path,self.walked_path,self.walked_times=[],[],[]
                    self.memory_aim=None
                self._observe_walk(position)
            result=self._next_target(position,aim,check,before_plan,approach_goal=target)
            self.last_decision={**self.last_decision,'kind':'observed_partial_memory',
                                'requested_target':tuple(target),'local_target':tuple(aim)}
            return result
        return planned if planned is not None else super().next_target(position,target,check,before_plan)
