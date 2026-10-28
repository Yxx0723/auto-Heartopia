"""Guidance-only scheduling over the existing local collision planner."""
from utils.core.coordinate_navigation import LocalNavigator, polygon_distance


class GuidanceNavigator(LocalNavigator):
    continuous_guidance = True

    def next_target(self, position, target, check=lambda:None, before_plan=lambda:None):
        provider = self.scene_provider
        snapshot = provider()
        if snapshot is not self.scene:
            before_plan()  # Signature work / scene invalidation may be expensive.
        self.scene_provider = lambda:snapshot
        self.before_refresh = before_plan
        try:
            return super().next_target(position,target,check,before_plan)
        finally:
            self.scene_provider = provider
            self.before_refresh = lambda:None

    def _build_map(self, *args):
        self.before_refresh()  # Also catches a height-layer or risk-driven rebuild.
        return super()._build_map(*args)

    def explain(self, position, target):
        """Bounded diagnostics; observations, not inferred physical contact."""
        if self.obstacles is None:
            return {'kind':'no_collision_map'}
        a,b = (position[0],position[2]),(target[0],target[2])
        blocked = []
        for polygon,clearance in self.obstacles.polygons:
            if polygon_distance(a,b,polygon) <= clearance:
                blocked.append({'footprint':polygon,'clearance':clearance})
                if len(blocked)==8:break
        return {'kind':'local_connection_rejected', 'agent_y':position[1],
                'start_clear':self.obstacles.clear(a,a),'target_clear':self.obstacles.clear(b,b),
                'direct_clear':self.obstacles.clear(a,b),'rejecting_footprints':blocked,
                'map_origin':self.obstacles.origin,'map_radius':self.obstacles.radius}
