"""Saved destinations over the proven local controller, without game guidance."""
from utils.core.guidance_navigation import GuidanceNavigator
from utils.core.traversal_evidence import scene_scope
from utils.core.coordinate_route import distance


class CollectionNavigator(GuidanceNavigator):
    continuous_collection = True
    continuous_guidance = False

    def __init__(self,*args,native_map_collection=False,**kwargs):
        if type(native_map_collection) is not bool:
            raise ValueError('采集原生引导开关无效')
        self.native_map_collection=native_map_collection
        self.native_scene_provider=None
        super().__init__(*args,respect_water=True,**kwargs)

    def _shore_target(self,position,target,check):
        # A saved collection destination is not a disposable guidance lookahead.
        # Preserve its exact arrival and interaction semantics, including water.
        return None

    def next_target(self,position,target,check=lambda:None,before_plan=lambda:None):
        if self.last_decision.get('kind')=='observed_partial_memory':
            self.last_decision={}
        library=getattr(self,'traversal_memory',None)
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
            result=self._next_target(position,aim,check,before_plan,approach_goal=target)
            self.last_decision={**self.last_decision,'kind':'observed_partial_memory',
                                'requested_target':tuple(target),'local_target':tuple(aim)}
            return result
        return planned if planned is not None else super().next_target(position,target,check,before_plan)
