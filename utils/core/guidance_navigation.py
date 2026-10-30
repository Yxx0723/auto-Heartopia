"""Guidance-only scheduling over the existing local collision planner."""
import math
import time

from utils.core import thresholds as policy
from utils.core.navigation_supply import navigation_expiry
from utils.core.guidance_anchor import GuidanceAnchor
from utils.core.local_collision_scope import local_collision_scene
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait, polygon_distance


class GuidanceNavigator(LocalNavigator):
    continuous_guidance = True

    def __init__(self, *args, scene_clock=time.monotonic, **kwargs):
        super().__init__(*args, **kwargs)
        self.scene_clock = scene_clock
        self.anchor = GuidanceAnchor()
        self._refresh_ticket = None
        self._in_call = False
        self.before_refresh = lambda: None
        self.release_reason = 'planning_or_map_change'
        self.last_timings = {}
        self.anchor_rejoin = False
        self._timings = {}

    def invalidate(self):
        super().invalidate()
        if hasattr(self, 'anchor'):
            self.anchor.reset()
        self._refresh_ticket = None

    def follow_target(self, cursor, position, aim, check, before_plan):
        selected = self.anchor.choose(cursor, position, aim)
        self.anchor_rejoin = False
        try:
            result = self.next_target(position, selected, check, before_plan)
            original = (aim['position'][0], aim['position'][2])
            self.anchor_rejoin = selected != aim['position'] and not self.obstacles.clear(original, original)
            return result
        except NavigationWait as exc:
            if exc.reason != 'no_path' or selected == aim['position']:
                raise
            self.anchor.reject(cursor)
            return self.next_target(position, aim['position'], check, before_plan)

    def _release_for(self, reason, callback):
        previous = self.release_reason
        self.release_reason = reason
        try:
            return callback()
        finally:
            self.release_reason = previous

    def _refresh_key(self, position):
        return (id(self.scene), id(self.obstacles), tuple(position), self.risks.revision,
                self.reference_revision)

    def _refresh_obstacles(self, position, target):
        if self._in_call and self._refresh_ticket == self._refresh_key(position) \
                and self.obstacles is not None and not self.probes and not self.risks.count \
                and getattr(self, 'slope', None) is None and getattr(self, 'coverage', None) is None:
            self.obstacles.set_goal(target)  # Goal-specific padding is never cached.
            self._timings['refresh_reuses'] += 1
            return self.clock()
        started = time.perf_counter()
        result = super()._refresh_obstacles(position, target)
        if self._in_call:
            self._timings['refresh_seconds'] += time.perf_counter()-started
            self._timings['refresh_calls'] += 1
            self._refresh_ticket = self._refresh_key(position)
        return result

    def collision_deadline(self, control_now):
        # Scene workers use monotonic; Windows Python 3.12 perf_counter has a
        # different origin. Transfer a remaining DURATION, never a raw timestamp.
        if self.scene is not None and 'quality' not in self.scene:
            return control_now+policy.COORD_NAV_SCENE_MAX_AGE  # Legacy fixtures.
        source_now = self.scene_clock()
        expiry = navigation_expiry(self.scene, source_now)
        return None if expiry is None else control_now+max(0., expiry-source_now)


    def next_target(self, position, target, check=lambda:None, before_plan=lambda:None):
        provider = self.scene_provider
        snapshot = provider()
        self.scene_provider = lambda:snapshot
        self.before_refresh = before_plan
        self._refresh_ticket = None
        self._in_call = True
        self._timings = {'refresh_seconds': 0., 'refresh_calls': 0, 'refresh_reuses': 0,
                         'map_build_seconds': 0., 'shore_seconds': 0., 'plan_and_path_seconds': 0.}
        started = time.perf_counter()
        plans_before, maps_before = self.stats['plans'], self.stats['map_builds']
        try:
            # The parent still owns scene validation, map invalidation and every
            # A*/shortcut collision check. No stale alternate-point cache exists.
            self._refresh_obstacles(position, target)
            phase = time.perf_counter()
            alternate = self._shore_target(position, target, check)
            self._timings['shore_seconds'] += time.perf_counter()-phase
            selected = target if alternate is None else alternate
            phase = time.perf_counter()
            result = super().next_target(position, selected, check,
                lambda: self._release_for('path_search', before_plan))
            self._timings['plan_and_path_seconds'] += time.perf_counter()-phase
            if alternate is not None:
                self.last_decision = {**self.last_decision,
                    'kind': 'guidance_shore_alternative',
                    'requested_target': tuple(target), 'local_target': alternate,
                    'offset': math.dist(alternate, target)}
            return result
        finally:
            self._in_call = False
            self._refresh_ticket = None  # Never reused by a later control tick.
            self.last_timings = {**self._timings, 'total_seconds': time.perf_counter()-started,
                'plans': self.stats['plans']-plans_before, 'map_builds': self.stats['map_builds']-maps_before,
                'anchor_changes': self.anchor.changes}
            self.scene_provider = provider
            self.before_refresh = lambda:None

    def _shore_target(self, position, target, check):
        """Only replace a dry local aim rejected by approximate water padding.

        This is a steering helper, never a destination/arrival edit. A candidate
        must also have a fully checked direct approach in the current map; no
        water, bridge, physical collider or learned-risk rule is relaxed.
        """
        obstacles = self.obstacles
        a, b = (position[0], position[2]), (target[0], target[2])
        if obstacles.clear(b, b) or not obstacles.clear(a, a):
            return None
        rejecting = []
        for index in obstacles.bins.get((math.floor(b[0]/4), math.floor(b[1]/4)), ()):
            check()
            polygon, clearance = obstacles.polygons[index]
            gap = polygon_distance(b, b, polygon)
            if gap > clearance:
                continue
            # Actual water and any non-water rejection keep the original wait.
            if index not in obstacles.water_polygon_ids or gap <= 1e-6:
                return None
            rejecting.append((polygon, clearance))
        if not rejecting:
            return None
        candidates = set()
        for polygon, clearance in rejecting:
            for c, d in zip(polygon, polygon[1:]+polygon[:1]):
                check()
                dx, dz = d[0]-c[0], d[1]-c[1]
                length = dx*dx+dz*dz
                if not length:
                    continue
                t = max(0., min(1., ((b[0]-c[0])*dx+(b[1]-c[1])*dz)/length))
                nearest = (c[0]+t*dx, c[1]+t*dz)
                gap = math.dist(b, nearest)
                if not 1e-6 < gap <= clearance:
                    continue
                scale = (clearance+policy.COORD_GUIDANCE_SHORE_MARGIN)/gap
                point = (nearest[0]+(b[0]-nearest[0])*scale,
                         nearest[1]+(b[1]-nearest[1])*scale)
                if math.dist(point, b) <= policy.COORD_GUIDANCE_CORRIDOR_RADIUS:
                    candidates.add(point)
        for point in sorted(candidates, key=lambda p:(math.dist(p,b), p))[
                :policy.COORD_GUIDANCE_SHORE_MAX_CANDIDATES]:
            check()
            if math.dist(a, point) <= 1e-6:
                continue  # Never turn the current position into an arrival.
            if obstacles.clear(point, point) and obstacles.clear(a, point) \
                    and not obstacles.risk_cost(a, point):
                return (point[0], target[1], point[1])
        return None

    def _build_map(self, *args):
        self._release_for('map_build', self.before_refresh)
        started = time.perf_counter()
        try:
            scene, position, target, radius = args
            local = local_collision_scene(scene, position, radius)
            if self._in_call:
                self._timings['map_input_colliders'] = len(scene.get('colliders', ()))
                self._timings['map_local_colliders'] = len(local['colliders'])
            return super()._build_map(local, position, target, radius)
        finally:
            if self._in_call:
                self._timings['map_build_seconds'] += time.perf_counter()-started

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
