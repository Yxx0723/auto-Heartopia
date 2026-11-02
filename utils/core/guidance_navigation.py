"""Guidance-only scheduling over the existing local collision planner."""
import math
import time

from utils.core import thresholds as policy
from utils.core.navigation_supply import navigation_expiry
from utils.core.guidance_anchor import GuidanceAnchor
from utils.core.local_collision_scope import local_collision_scene
from utils.core.prepared_navigation import PreparedNavigationScene
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait, polygon_distance
from utils.core.coordinate_route import direction_keys, key_vector
from utils.core.terrain_sections import section_scene, terrain_profile_signature
from utils.core.terrain_wall_profiles import TERRAIN_SECTION_REVISION


class GuidanceNavigator(LocalNavigator):
    continuous_guidance = True

    def __init__(self, *args, scene_clock=time.monotonic, respect_water=True, **kwargs):
        if type(respect_water) is not bool:
            raise ValueError('Invalid guidance water policy')
        self._respect_water = respect_water
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
        self._terrain_height_gates = ()
        self._recovery_height_gates = ()
        self._recovery_terrain_signature = None
        self._steering_state = None
        self.steering_stats = {'switches': 0, 'holds': 0, 'rejected_holds': 0}

    @property
    def respect_water(self):
        # Constructor-only: a live map/path cannot silently change policy.
        return self._respect_water

    def reset_steering(self):
        self._steering_state = None

    def steering_keys(self, position, target, basis, travel, obstacles=None, reach_radius=None):
        """Dampen adjacent-sector chatter, never keep an unchecked WASD pulse.

        No timer forces a direction to be held. Significant turns, stale state,
        a changed camera/layer/world, arrival and recovery use ordinary steering.
        The parent rechecks physical clearance and learned risks even on a hold.
        """
        chosen = super().steering_keys(position, target, basis, travel,
                                       obstacles=obstacles, reach_radius=reach_radius)
        old = self._steering_state
        if not chosen or self.obstacles is None or obstacles is not None:
            self.reset_steering()
            return chosen
        now = self.clock()
        context = ((self.scene or {}).get('cache_context'),
                   (self.scene or {}).get('terrain_revision'))
        camera = tuple(basis['forward'])
        nominal = direction_keys(position, target, basis, reach_radius=reach_radius)

        def angle(a, b):
            return math.degrees(math.acos(max(-1., min(1.,
                sum(x*y for x,y in zip(a,b))/math.hypot(*a)/math.hypot(*b)))))

        if old is not None and chosen != old['keys'] and chosen == nominal \
                and 0. <= now-old['at'] <= policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS \
                and context == old['context'] \
                and math.dist(position, old['position']) <= policy.COORD_DIRECTION_POSITION_TOLERANCE \
                and abs(position[1]-old['height']) < policy.COORD_NAV_HEIGHT_REFRESH \
                and angle(camera, old['camera']) <= policy.COORD_DIRECTION_STABLE_DEGREES:
            previous = key_vector(old['keys'], basis)
            proposed = key_vector(chosen, basis)
            desired = (target[0]-position[0], target[2]-position[2])
            if angle(previous, proposed) <= 45.+1e-6 \
                    and angle(previous, desired) < 45. \
                    and angle(previous, desired)-angle(proposed, desired) \
                        <= policy.COORD_GUIDANCE_STEERING_HYSTERESIS_DEGREES:
                checked = super().steering_keys(position, target, basis, travel,
                    reach_radius=reach_radius, preferred_keys=old['keys'])
                if checked == old['keys']:
                    chosen = checked
                    self.steering_stats['holds'] += 1
                else:
                    self.steering_stats['rejected_holds'] += 1
        if old is not None and chosen != old['keys']:
            self.steering_stats['switches'] += 1
        self._steering_state = {'keys': frozenset(chosen), 'camera': camera,
                               'position': tuple(position), 'height': position[1],
                               'context': context, 'at': now}
        return chosen

    def invalidate(self):
        super().invalidate()
        if hasattr(self, 'anchor'):
            self.anchor.reset()
        self.reset_steering()
        self._terrain_height_gates = ()
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

    def _geometry_signature(self, scene):
        started = time.perf_counter()
        prepared = scene.prepared_signature() if isinstance(scene, PreparedNavigationScene) else None
        result = prepared if prepared is not None else super()._geometry_signature(scene)
        terrain = scene.prepared_terrain_signature() if prepared is not None else terrain_profile_signature(scene)
        if self._in_call:
            self._timings['signature_seconds'] = time.perf_counter()-started
            self._timings['signature_prepared'] = prepared is not None
        return result, terrain

    def _refresh_key(self, position):
        return (id(self.scene), id(self.obstacles), tuple(position), self.risks.revision,
                self.reference_revision)

    def _refresh_obstacles(self, position, target):
        # A previously skipped source face may enter the body band before the
        # ordinary .75 height refresh. Do not retain that stale classification.
        if self.obstacles is not None and any(
                (low < position[1] < high) != (low < self.obstacles.height < high)
                for low, high in self._terrain_height_gates):
            self.obstacles, self.path, self.retry_at = None, [], 0.
            self._refresh_ticket = None
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
            if alternate is not None:
                self._timings['local_aim_offset'] = math.dist(alternate, target)
            selected = target if alternate is None else alternate
            phase = time.perf_counter()
            result = super().next_target(position, selected, check,
                lambda: self._release_for('path_search', before_plan))
            self._timings['plan_and_path_seconds'] += time.perf_counter()-phase
            if alternate is not None:
                self.last_decision = {**self.last_decision,
                    'kind': ('guidance_shore_alternative' if self.respect_water
                             else 'guidance_padding_alternative'),
                    'requested_target': tuple(target), 'local_target': alternate,
                    'offset': math.dist(alternate, target)}
            return result
        finally:
            self._in_call = False
            self._refresh_ticket = None  # Never reused by a later control tick.
            self.last_timings = {**self._timings, 'total_seconds': time.perf_counter()-started,
                'plans': self.stats['plans']-plans_before, 'map_builds': self.stats['map_builds']-maps_before,
                'anchor_changes': self.anchor.changes, 'respect_water': self.respect_water}
            self.scene_provider = provider
            self.before_refresh = lambda:None

    def _shore_target(self, position, target, check):
        """Replace an outside-body local aim rejected only by artificial padding.

        This is a steering helper, never a destination/arrival edit. A candidate
        must have a fully checked direct approach in the current map. The legacy
        policy only handles water margins; water-ignoring guidance may make a
        much smaller adjustment near solid geometry. No clearance is reduced.
        """
        obstacles = self.obstacles
        a, b = (position[0], position[2]), (target[0], target[2])
        if obstacles.clear(b, b) or math.dist(a, b) <= policy.COORD_REACH_RADIUS \
                or (self.respect_water and not obstacles.clear(a, a)):
            return None
        rejecting = []
        for index in obstacles.bins.get((math.floor(b[0]/4), math.floor(b[1]/4)), ()):
            check()
            polygon, clearance = obstacles.polygons[index]
            gap = polygon_distance(b, b, polygon)
            if gap > clearance:
                continue
            # No aim inside physical geometry can be reclassified as padding.
            if gap <= 1e-6 or (self.respect_water and index not in obstacles.water_polygon_ids):
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
                limit = policy.COORD_GUIDANCE_CORRIDOR_RADIUS
                if not self.respect_water:
                    limit = min(limit, policy.COORD_GUIDANCE_PADDING_MAX_OFFSET)
                if math.dist(point, b) <= limit:
                    candidates.add(point)
        for point in sorted(candidates, key=lambda p:(math.dist(p,b), p))[
                :policy.COORD_GUIDANCE_SHORE_MAX_CANDIDATES]:
            check()
            if math.dist(a, point) <= 1e-6:
                continue  # Never turn the current position into an arrival.
            if obstacles.clear(point, point) and obstacles.clear(a, point, escape=True) \
                    and not obstacles.risk_cost(a, point):
                return (point[0], target[1], point[1])
        return None

    def _build_map(self, *args):
        self._release_for('map_build', self.before_refresh)
        started = time.perf_counter()
        try:
            scene, position, target, radius = args
            local = local_collision_scene(scene, position, radius)
            self._terrain_height_gates = self._height_gates(local)
            local = section_scene(local, position[1])
            if self._in_call:
                self._timings['terrain_sections'] = TERRAIN_SECTION_REVISION
                self._timings['map_input_colliders'] = len(scene.get('colliders', ()))
                self._timings['map_local_colliders'] = len(local['colliders'])
            return super()._build_map(local, position, target, radius)
        finally:
            if self._in_call:
                self._timings['map_build_seconds'] += time.perf_counter()-started

    def recovery_keys(self, position, target, basis, travel):
        provider = self.scene_provider
        scene = provider()
        signature = (scene.prepared_terrain_signature() if isinstance(scene, PreparedNavigationScene)
                     else terrain_profile_signature(scene)) if scene else None
        if signature is None and scene:
            signature = terrain_profile_signature(scene)
        if signature != self._recovery_terrain_signature:
            self.water_guard = None
        local = local_collision_scene(scene, position, policy.COORD_NAV_SEARCH_RADIUS) if scene else scene
        if self.water_guard is not None and any(
                (low < position[1] < high) != (low < self.water_guard.height < high)
                for low, high in self._recovery_height_gates):
            self.water_guard = None
        old = self.water_guard
        self.scene_provider = lambda: section_scene(local, position[1]) if local else local
        try:
            result = super().recovery_keys(position, target, basis, travel)
            if self.water_guard is not old and local:
                self._recovery_height_gates = self._height_gates(local)
                self._recovery_terrain_signature = signature
            return result
        finally:
            self.scene_provider = provider

    @staticmethod
    def _height_gates(scene):
        return tuple((box['height'][0]-policy.COORD_NAV_BODY_HEIGHT,
                      box['height'][1]-policy.COORD_NAV_GROUND_CLEARANCE)
                     for box in scene['colliders'] if box.get('source') == 'verified-main-terrain-mesh'
                     and isinstance(box.get('height'), (tuple, list)) and len(box['height']) == 2
                     and all(type(v) in (int, float) and math.isfinite(v) for v in box['height']))

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
