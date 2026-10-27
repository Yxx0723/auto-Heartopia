"""Local XZ navigation over partial scene geometry; never treats it as a NavMesh.

No game calls or input here. Exact segment clearance protects A* edges and
shortcuts. The follower supplies interruption checks and releases input before
expensive searches. Missing geometry means wait, rather than silently driving straight.
"""
import heapq
import itertools
import math
import time

from utils.core import thresholds as policy
from utils.core.approach_risk import ApproachRisks, local_geometry
from utils.core.bridge_navigation import detect_bridge_decks, geometry_key, segment_uses_bridge
from utils.core.coordinate_route import direction_keys, distance, key_vector, valid_position
from utils.core.navigation_water import NAVIGATION_WATER_AREAS as WATER_AREAS
from utils.core.navigation_reference import ReferenceRisks
from utils.core.scene_map import collider_footprint


class NavigationWait(Exception):
    """No fresh geometry or no route; retry without advancing the saved point."""
    def __init__(self, message, reason='no_path'):
        super().__init__(message)
        self.reason = reason


def point_segment_distance(point, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dz * dz
    t = max(0., min(1., ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / length)) if length else 0.
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dz)


def inside(point, polygon):
    x, z = point
    result = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > z) != (b[1] > z) and x < (b[0] - a[0]) * (z - a[1]) / (b[1] - a[1]) + a[0]:
            result = not result
    return result


def segments_cross(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    ab_c, ab_d, cd_a, cd_b = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    if ab_c * ab_d < 0 and cd_a * cd_b < 0:
        return True
    return any(abs(value) < 1e-9 and point_segment_distance(p, u, v) < 1e-9
               for value, p, u, v in ((ab_c, c, a, b), (ab_d, d, a, b), (cd_a, a, c, d), (cd_b, b, c, d)))


def polygon_distance(a, b, polygon):
    if inside(a, polygon) or inside(b, polygon):
        return 0.
    result = math.inf
    for c, d in zip(polygon, polygon[1:] + polygon[:1]):
        if segments_cross(a, b, c, d):
            return 0.
        result = min(result, point_segment_distance(a, c, d), point_segment_distance(b, c, d),
                     point_segment_distance(c, a, b), point_segment_distance(d, a, b))
    return result


def scene_geometry_signature(scene):
    """Ignore timestamps, object labels and collider order when geometry agrees."""
    result = set()
    reference = scene.get('navigation_reference')
    if reference:
        result.add(('navigation-reference', reference['revision']))
    for polygon in scene.get('navigation_exclusions', ()):
        if isinstance(polygon, (list, tuple)) and len(polygon) >= 3 and all(
                isinstance(p, (list, tuple)) and len(p) == 2 and all(
                    type(v) in (int, float) and math.isfinite(v) for v in p) for p in polygon):
            result.add(('navigation-exclusion', tuple(tuple(p) for p in polygon)))
    for box in scene.get('colliders', ()):
        heights, polygon = box.get('height'), collider_footprint(box)
        if not isinstance(heights, (list, tuple)) or len(heights) != 2 \
                or not all(type(v) in (int, float) and math.isfinite(v) for v in heights) \
                or polygon is None:
            continue
        result.add((tuple(heights), tuple(tuple(p) for p in polygon),
                    box.get('category'), box.get('source')))
    return frozenset(result)


class ObstacleMap:
    def __init__(self, scene, position, probes=(), water_areas=WATER_AREAS, goal=None, search_radius=None, risks=None):
        self.height = position[1]
        self.risks = risks
        self.origin = tuple(position)
        self.search_radius = policy.COORD_NAV_SEARCH_RADIUS if search_radius is None else search_radius
        self.radius = self.search_radius + policy.COORD_NAV_LEG_LENGTH
        self.polygons, self.bins = [], {}
        water_areas = tuple(water_areas)
        self.bridge_decks = detect_bridge_decks(scene, position, water_areas)
        self.reference_risks = ReferenceRisks(scene.get('navigation_reference'), self.height,
                                              position, self.radius, self.bridge_decks)
        self.bridge_geometry_keys = {deck['key'] for deck in self.bridge_decks}
        self.water_polygon_ids = {}
        self.approachable, self.terminal_padding, self.goal = set(), {}, None
        candidates = []
        for box in scene.get('colliders', []):
            if geometry_key(box) in self.bridge_geometry_keys:
                continue  # The recognized deck is a support surface, not a body obstacle.
            heights = box.get('height')
            if not isinstance(heights, (list, tuple)) or len(heights) != 2 \
                    or not all(type(v) in (float, int) and math.isfinite(v) for v in heights):
                continue
            # Ground and overhead geometry must not block the entire XZ map.
            if heights[1] <= position[1] + policy.COORD_NAV_GROUND_CLEARANCE \
                    or heights[0] >= position[1] + policy.COORD_NAV_BODY_HEIGHT:
                continue
            footprint = collider_footprint(box)
            if footprint is None:
                continue
            segment = len(footprint) == 2
            candidates.append((footprint, policy.COORD_NAV_CLEARANCE,
                               box.get('category') != 'mountain' and not segment, None, segment))
        candidates.extend((polygon, policy.COORD_NAV_WATER_CLEARANCE, False, water_index, False)
                          for water_index, polygon in enumerate(water_areas))
        candidates.extend((polygon, policy.COORD_PATROL_HOME_MARGIN, False, None, False)
                          for polygon in scene.get('navigation_exclusions', ()))
        candidates.extend((polygon, policy.COORD_NAV_CLEARANCE / 2, False, None, False) for polygon in probes)
        for polygon, clearance, approachable, water_index, segment in candidates:
            if not isinstance(polygon, (list, tuple)) or len(polygon) < (2 if segment else 3) \
                    or any(not isinstance(p, (list, tuple)) or len(p) != 2
                           or not all(type(v) in (float, int) and math.isfinite(v) for v in p) for p in polygon):
                continue
            polygon = tuple(tuple(p) for p in polygon)
            xs, zs = zip(*polygon)
            left, right, low, high = min(xs) - clearance, max(xs) + clearance, min(zs) - clearance, max(zs) + clearance
            radius = self.radius
            if right < position[0] - radius or left > position[0] + radius \
                    or high < position[2] - radius or low > position[2] + radius:
                continue
            index = len(self.polygons)
            self.polygons.append((polygon, clearance))
            if water_index is not None:
                self.water_polygon_ids[index] = water_index
            if approachable:
                self.approachable.add(index)
            # Clip indexing to the local search area, even for a very large box.
            for x in range(math.floor(max(left, position[0] - radius) / 4), math.floor(min(right, position[0] + radius) / 4) + 1):
                for z in range(math.floor(max(low, position[2] - radius) / 4), math.floor(min(high, position[2] + radius) / 4) + 1):
                    self.bins.setdefault((x, z), set()).add(index)
        if goal is not None:
            self.set_goal(goal)

    def set_goal(self, goal):
        point = (goal[0], goal[2])
        if point == self.goal:
            return
        self.goal, self.terminal_padding = point, {}
        for index in self.bins.get((math.floor(point[0] / 4), math.floor(point[1] / 4)), ()):
            if index not in self.approachable:
                continue
            polygon, clearance = self.polygons[index]
            gap = polygon_distance(point, point, polygon)
            if policy.COORD_NAV_APPROACH_MIN_GAP < gap < clearance:
                self.terminal_padding[index] = max(policy.COORD_NAV_APPROACH_MIN_GAP, gap - .02)

    def clear(self, a, b, escape=False):
        if any(abs(p[0] - self.origin[0]) > self.radius or abs(p[1] - self.origin[2]) > self.radius for p in (a, b)):
            return False  # Space outside the indexed map is not proven clear.
        indexes = set()
        for x in range(math.floor(min(a[0], b[0]) / 4), math.floor(max(a[0], b[0]) / 4) + 1):
            for z in range(math.floor(min(a[1], b[1]) / 4), math.floor(max(a[1], b[1]) / 4) + 1):
                indexes.update(self.bins.get((x, z), ()))
        for index in indexes:
            polygon, clearance = self.polygons[index]
            # The recorded player stood here. Reduce only artificial padding
            # during a short final approach, never the physical polygon itself.
            if index in self.terminal_padding and max(math.dist(a, self.goal), math.dist(b, self.goal)) \
                    <= policy.COORD_NAV_APPROACH_RADIUS:
                clearance = self.terminal_padding[index]
            separation = polygon_distance(a, b, polygon)
            if separation > clearance:
                continue
            water_index = self.water_polygon_ids.get(index)
            if water_index is not None and any(
                    segment_uses_bridge(deck, water_index, a, b, polygon, clearance,
                                        policy.COORD_NAV_CLEARANCE)
                    for deck in self.bridge_decks):
                continue
            # A player already inside the padding may step outward. Never
            # permit crossing the actual polygon or moving deeper into padding.
            start_gap = polygon_distance(a, a, polygon)
            end_gap = polygon_distance(b, b, polygon)
            if escape and start_gap > 1e-6 and separation >= start_gap - 1e-8 and end_gap > start_gap + 1e-6:
                continue
            return False
        return True

    def covers_search(self, position, radius):
        return max(abs(position[0] - self.origin[0]), abs(position[2] - self.origin[2])) + radius <= self.radius

    def risk_cost(self, start, end):
        return (self.risks.cost(start, end, self.height) if self.risks is not None else 0.) \
            + self.reference_risks.cost(start, end)

    def travel_cost(self, start, end):
        return math.dist(start,end) + self.risk_cost(start,end)


def plan_path(position, target, obstacles, check=lambda: None, *, grid_step=None, search_radius=None,
              max_nodes=None, horizon=None, approach_goal=None, before_search=lambda: None):
    """A bounded A* plan to an exact local endpoint, or a useful far-goal frontier."""
    if not valid_position(position) or not valid_position(target):
        raise ValueError('寻路坐标无效')
    start, goal = (position[0], position[2]), (target[0], target[2])
    obstacles.set_goal(target if approach_goal is None else approach_goal)
    grid_step = policy.COORD_NAV_GRID_STEP if grid_step is None else grid_step
    search_radius = policy.COORD_NAV_SEARCH_RADIUS if search_radius is None else search_radius
    max_nodes = policy.COORD_NAV_MAX_NODES if max_nodes is None else max_nodes
    horizon = policy.COORD_NAV_HORIZON if horizon is None else horizon
    remaining = math.dist(start, goal)
    if remaining <= horizon and not obstacles.clear(goal, goal):
        raise NavigationWait('目标附近暂不可通行，正在重新寻路')
    local = goal if remaining <= horizon else tuple(
        a + (b - a) * horizon / remaining for a, b in zip(start, goal))
    check()
    if obstacles.clear(start, local, escape=True) and obstacles.risk_cost(start,local) == 0:
        raw = [start, local]
    else:
        before_search()
        check()
        step = grid_step
        limit = int(search_radius / step)
        world = lambda node: (start[0] + node[0] * step, start[1] + node[1] * step)
        counter = itertools.count()
        frontier = [(math.dist(start, local), next(counter), (0, 0), 0.)]
        costs, parents, closed = {(0, 0): 0.}, {}, set()
        best, reached, reached_cost = (0, 0), None, math.inf
        point_cache, edge_cache = {}, {}
        offsets = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]

        def free(node):
            if node not in point_cache:
                point_cache[node] = obstacles.clear(world(node), world(node))
            return point_cache[node]

        while frontier and len(closed) < max_nodes:
            if reached is not None and frontier[0][0] >= reached_cost-1e-9:
                break
            if len(closed) % 32 == 0:
                check()
            _, _, node, cost = heapq.heappop(frontier)
            if node in closed or cost > costs[node]:
                continue
            closed.add(node)
            point = world(node)
            if math.dist(point, goal) < math.dist(world(best), goal):
                best = node
            if math.dist(point, local) <= step and obstacles.clear(point, local):
                candidate_cost = cost+obstacles.travel_cost(point,local)
                if candidate_cost < reached_cost:
                    reached, reached_cost = node, candidate_cost
            for dx, dz in offsets:
                neighbor = (node[0] + dx, node[1] + dz)
                if max(abs(neighbor[0]), abs(neighbor[1])) > limit or neighbor in closed:
                    continue
                if not free(neighbor):
                    continue
                if dx and dz and (not free((node[0] + dx, node[1])) or not free((node[0], node[1] + dz))):
                    continue
                edge = (node, neighbor)
                if edge not in edge_cache:
                    edge_cache[edge] = obstacles.clear(point, world(neighbor), escape=node == (0, 0))
                if not edge_cache[edge]:
                    continue
                new_cost = cost + obstacles.travel_cost(point,world(neighbor))
                if new_cost >= costs.get(neighbor, math.inf):
                    continue
                costs[neighbor], parents[neighbor] = new_cost, node
                heapq.heappush(frontier, (new_cost + math.dist(world(neighbor), local), next(counter), neighbor, new_cost))
        found_local = reached is not None
        if reached is None:
            if remaining <= horizon or remaining - math.dist(world(best), goal) < policy.COORD_NAV_MIN_PROGRESS:
                raise NavigationWait('暂未找到可通行路径，正在重新寻路')
            reached = best
        nodes = [reached]
        while nodes[-1] != (0, 0):
            nodes.append(parents[nodes[-1]])
        raw = [world(node) for node in reversed(nodes)]
        if obstacles.clear(raw[-1], local) and (found_local or obstacles.risk_cost(raw[-1],local) == 0):
            raw.append(local)
    # Greedy visible shortcuts, followed by short fixed steering legs. No
    # untested smoothing curve can cut a corner or pass through a thin wall.
    result, index = [], 0
    while index < len(raw) - 1:
        check()
        end = index + 1
        for candidate in range(index + 2, len(raw)):
            corridor_cost = sum(obstacles.travel_cost(a,b) for a,b in zip(raw[index:candidate],raw[index+1:candidate+1]))
            if obstacles.clear(raw[index], raw[candidate], escape=index == 0) and obstacles.travel_cost(
                    raw[index],raw[candidate]) <= corridor_cost+1e-8:
                end = candidate
        length = math.dist(raw[index], raw[end])
        count = max(1, math.ceil(length / policy.COORD_NAV_LEG_LENGTH))
        for part in range(1, count + 1):
            x, z = (a + (b - a) * part / count for a, b in zip(raw[index], raw[end]))
            result.append((x, position[1], z))
        index = end
    return result


class LocalNavigator:
    def __init__(self, scene_provider, clock=time.monotonic, water_areas=WATER_AREAS, path_memory=None):
        self.scene_provider, self.clock, self.water_areas = scene_provider, clock, water_areas
        self.scene, self.obstacles, self.goal = None, None, None
        self.scene_signature = None
        self.reference_revision = None
        self.path, self.probes = [], []
        self.retry_at = 0.
        self.path_memory = path_memory
        self.waypoint = None
        self.memory_leg = None
        self.learning_time = None
        self.learning_started = None
        self.learning_finished = None
        self.memory_path, self.walked_path = [], []
        self.walked_times = []
        self.collection_outcome = None
        self.memory_aim = None
        self.memory_loaded, self.memory_failed = False, False
        self.memory_state = 'learning' if path_memory else 'off'
        self.search_radius = policy.COORD_NAV_SEARCH_RADIUS
        self.stats = {'map_builds': 0, 'plans': 0}
        self.water_guard = None
        self.water_guard_context = None
        self.risks = ApproachRisks(clock=clock)
        self.risk_revision = self.risks.revision
        self.last_decision = {}

    def set_waypoint(self, index):
        if self.waypoint != index:
            self.waypoint = index
            self.memory_leg = None
            self.search_radius = policy.COORD_NAV_SEARCH_RADIUS

    def arrived(self, position):
        if not self.path_memory or self.waypoint is None or self.memory_failed:
            return
        self.learning_time = self.learning_finished
        self._observe_walk(position)
        if not self.memory_failed:
            if self.walked_path and self.walked_path[-1] != tuple(position):
                self.walked_path.append(tuple(position))
                self.walked_times.append(self._learning_elapsed())
            try:
                if self.path_memory.remember(self.waypoint, self.walked_path,
                        (self.learning_finished - self.learning_started)
                        if self.learning_finished is not None and self.learning_started is not None else None,
                        sample_times=self.walked_times, collection_outcome=self.collection_outcome):
                    self.memory_state = 'saved'
            except (OSError, ValueError):
                self.memory_state = 'write_error'
        self.walked_path = []
        self.walked_times = []
        self.memory_leg = None

    def _learning_elapsed(self):
        if self.learning_time is None or self.learning_started is None:
            return None
        return max(0., self.learning_time-self.learning_started)

    def discard_learning(self):
        """A commanded jump is not ordinary walking evidence."""
        if self.memory_leg is not None:
            self.memory_failed = True
            self.walked_path, self.walked_times = [], []

    def _observe_walk(self, position):
        if self.memory_failed:
            return
        if self.walked_path and math.dist(position, self.walked_path[-1]) > 6.:
            self.memory_failed = True  # Manual teleport / discontinuity is not a reusable walk.
            self.memory_path = []
            self.memory_aim = None
            return
        if not self.walked_path or math.dist(position, self.walked_path[-1]) >= .4:
            if len(self.walked_path) >= 2047:
                self.memory_failed = True
                self.memory_path = []
                self.memory_aim = None
            else:
                self.walked_path.append(tuple(position))
                self.walked_times.append(self._learning_elapsed())

    def invalidate(self):
        self.path, self.goal, self.obstacles = [], None, None
        self.retry_at = 0.

    def motion_clear(self, before, after):
        return self.obstacles is not None and self.obstacles.clear(
            (before[0], before[2]), (after[0], after[2]), escape=True)

    def steering_keys(self, position, target, basis, travel, obstacles=None, reach_radius=None):
        """Check the actual quantized WASD pulse, not just the ideal path line.

        A tangent path beside a river can round to a diagonal into the water.
        Prefer the usual direction, falling back to the best clear direction.
        Moving outward from existing padding uses the same escape rule as A*.
        """
        preferred = direction_keys(position, target, basis, reach_radius=reach_radius)
        obstacles = self.obstacles if obstacles is None else obstacles
        if not preferred or obstacles is None:
            return preferred
        start = (position[0], position[2])
        delta = (target[0] - position[0], target[2] - position[2])

        def candidate(keys):
            vector = key_vector(keys, basis)
            norm = math.hypot(*vector)
            unit = (vector[0] / norm, vector[1] / norm)
            end = tuple(a + b * travel for a, b in zip(start, unit))
            # A short quantized WASD pulse can aim back into a rejected entry
            # even when the ideal path bends away. Project only the risk query;
            # physical clearance still checks the actual pulse distance.
            risk_end = tuple(a+b*max(travel, policy.COORD_APPROACH_FOCUS_DISTANCE
                                    + policy.COORD_APPROACH_REPEAT_LATERAL) for a,b in zip(start,unit))
            repeated = self.risks.repeats_failed_entry(start, risk_end, position[1])
            return (obstacles.clear(start, end, escape=True) and not repeated,
                    sum(a * b for a, b in zip(delta, unit)) - obstacles.risk_cost(start,end))

        preferred_vector = key_vector(preferred,basis)
        norm = math.hypot(*preferred_vector)
        preferred_end = tuple(a+b/norm*travel for a,b in zip(start,preferred_vector))
        if candidate(preferred)[0] and obstacles.risk_cost(start,preferred_end) == 0:
            return preferred
        alternatives = [(score, set(keys)) for keys in ('w', 'd', 's', 'a', 'wd', 'ds', 'sa', 'aw')
                        for clear, score in [candidate(set(keys))] if clear]
        return max(alternatives, key=lambda item: item[0])[1] if alternatives else set()

    def recovery_keys(self, position, target, basis, travel):
        """A jump may probe a low obstacle, but never known water or terrain walls."""
        scene = self.scene_provider()
        if not scene or scene.get('state') != 'ready':
            return None
        context = (scene.get('cache_context'), scene.get('terrain_revision'))
        if self.water_guard is None or self.water_guard_context != context \
                or abs(position[1] - self.water_guard.height) >= policy.COORD_NAV_HEIGHT_REFRESH \
                or not self.water_guard.covers_search(position, travel):
            walls = [box for box in scene.get('colliders', ())
                     if box.get('source') == 'verified-main-terrain-mesh']
            self.water_guard = ObstacleMap({'colliders': walls}, position, water_areas=self.water_areas)
            self.water_guard_context = context
        return self.steering_keys(position, target, basis, travel, obstacles=self.water_guard)

    def blocked(self, position, target, *, attempt_id=None, mode='walk'):
        if self.path_memory:
            self.path_memory.reject_selected()
        self.memory_path = []
        self.memory_aim = None
        self.memory_state = 'learning' if self.path_memory else 'off'
        self.risks.record(position,target,attempt_id=attempt_id,mode=mode,
                          geometry=local_geometry(self.obstacles,position))
        self.last_decision = {'kind':'approach_failed', 'risk_revision':self.risks.revision,
                              'support_ref':None, 'physical_wall_confirmed':False}
        self.invalidate()

    def motion_succeeded(self, before, after, mode='walk'):
        self.risks.success(before,after,mode=mode)

    def next_target(self, position, target, check=lambda: None, before_plan=lambda: None):
        if self.path_memory is None or self.waypoint is None:
            return self._next_target(position, target, check, before_plan)
        leg = (self.waypoint, tuple(target))
        if self.memory_leg != leg:
            self.memory_leg = leg
            self.learning_started = self.learning_time
            self.learning_finished = None
            self.memory_loaded, self.memory_failed = False, False
            self.memory_path, self.walked_path = [], []
            self.walked_times = []
            self.memory_aim = None
            self.memory_state = 'learning'
        self._observe_walk(position)
        if not self.memory_loaded:
            self.memory_loaded = True
            if not self.memory_failed:
                before_plan()
                self._refresh_obstacles(position, target)
                def can_connect(point):
                    a, b = (position[0], position[2]), (point[0], point[2])
                    return self.obstacles.clear(a, b, escape=True) and self.obstacles.risk_cost(a, b) == 0
                self.memory_path = self.path_memory.lookup(self.waypoint, position, can_connect=can_connect)
        if self.memory_aim is not None and distance(position, self.memory_aim) <= policy.COORD_NAV_LEG_REACH_RADIUS:
            end = self.memory_path.index(self.memory_aim)
            del self.memory_path[:end + 1]
            self.memory_aim = None
        if self.memory_aim is None:
            while self.memory_path and distance(position, self.memory_path[0]) <= policy.COORD_NAV_LEG_REACH_RADIUS:
                self.memory_path.pop(0)
        if self.memory_path:
            self._refresh_obstacles(position, target)
            if self.memory_aim is None:
                self.memory_aim = self._memory_target(position)
            via = self.memory_aim
            try:
                result = self._next_target(position, via, check, before_plan, approach_goal=target)
                # Check the currently loaded corridor. Farther trace segments
                # are checked later as scene coverage follows the character.
                if self.obstacles.clear((position[0], position[2]), (via[0], via[2]), escape=True) and self.obstacles.risk_cost(
                        (position[0],position[2]),(via[0],via[2])) == 0:
                    self.memory_state = 'reusing'
                    return result
            except NavigationWait as exc:
                if exc.reason == 'scene':
                    raise
            self.path_memory.reject_selected()
            self.memory_path = []
            self.memory_aim = None
            self.invalidate()
        self.memory_state = 'learning'
        return self._next_target(position, target, check, before_plan)

    def _memory_target(self, position):
        """Look ahead only within the recorded corridor, including hidden bends."""
        start = (position[0], position[2])
        selected = self.memory_path[0]
        for index, point in enumerate(self.memory_path):
            if distance(position, point) > policy.COORD_NAV_MEMORY_LOOKAHEAD:
                break
            end = (point[0], point[2])
            if any(point_segment_distance((p[0], p[2]), start, end) > policy.COORD_NAV_MEMORY_CHORD_ERROR
                   for p in self.memory_path[:index]):
                break
            if not self.obstacles.clear(start, end, escape=True):
                break
            if self.obstacles.risk_cost(start,end) > 0:
                break
            selected = point
        return selected

    def _build_map(self, scene, position, target, radius):
        self.stats['map_builds'] += 1
        return ObstacleMap(scene, position, [p for _, p in self.probes], self.water_areas,
                           goal=target, search_radius=radius, risks=self.risks)

    def _refresh_obstacles(self, position, target):
        scene = self.scene_provider()
        if not scene or scene.get('state') != 'ready':
            # A missing sample is not a world switch. Keep short-lived evidence
            # unavailable to steering until the ready snapshot is revalidated.
            self.risks.expire(self.clock())
            if scene and scene.get('cache_context') is not None and 'terrain_revision' in scene:
                self.risks.bind(repr((scene['cache_context'], scene['terrain_revision'])))
            self.invalidate()
            raise NavigationWait('等待最新场景数据，稍后自动寻路', reason='scene')
        now = self.clock()
        reference_revision = (scene.get('navigation_reference') or {}).get('revision')
        if reference_revision != self.reference_revision:
            # A still-clear old path may now be much riskier than a detour.
            self.path, self.obstacles, self.retry_at = [], None, 0.
        self.risks.bind(repr((scene.get('cache_context'),scene.get('terrain_revision'))))
        self.risks.expire(now)
        if self.risk_revision != self.risks.revision:
            self.path, self.obstacles, self.retry_at = [], None, 0.
        current = (position[0], position[2])
        probes = [probe for probe in self.probes if probe[0] > now
                  and polygon_distance(current, current, probe[1]) > 1e-6]
        if len(probes) != len(self.probes):
            self.obstacles = None
        self.probes = probes
        signature = scene_geometry_signature(scene) if scene is not self.scene \
            or reference_revision != self.reference_revision else self.scene_signature
        self.reference_revision = reference_revision
        changed = signature != self.scene_signature
        self.scene, self.scene_signature = scene, signature
        if changed or self.obstacles is None \
                or abs(position[1] - self.obstacles.height) >= policy.COORD_NAV_HEIGHT_REFRESH:
            self.obstacles = self._build_map(scene, position, target, max(policy.COORD_NAV_SEARCH_RADIUS, self.search_radius))
            before_revision = self.risks.revision
            self.risks.revalidate(self.obstacles)
            if before_revision != self.risks.revision:
                self.path = []
            previous = (position[0], position[2])
            for index, point in enumerate(self.path):
                following = (point[0], point[2])
                if not self.obstacles.clear(previous, following, escape=index == 0):
                    self.path = []
                    break
                previous = following
            self.retry_at = 0.
        self.obstacles.set_goal(target)
        self.risk_revision = self.risks.revision
        return now

    def _next_target(self, position, target, check=lambda: None, before_plan=lambda: None, approach_goal=None):
        approach_goal = target if approach_goal is None else approach_goal
        now = self._refresh_obstacles(position, approach_goal)
        current = (position[0], position[2])
        if self.goal != tuple(target):
            self.path, self.goal, self.retry_at = [], tuple(target), 0.
            self.obstacles.set_goal(approach_goal)
        # A jump or a manual correction may pass several temporary legs. These
        # are navigation helpers, never saved collection points.
        for index in range(len(self.path) - 1, -1, -1):
            point = self.path[index]
            final = (point[0], point[2]) == (target[0], target[2])
            reached = distance(position, point) <= (policy.COORD_REACH_RADIUS
                       if final else policy.COORD_NAV_LEG_REACH_RADIUS)
            if not reached and index + 1 < len(self.path):
                following = self.path[index + 1]
                a, b = (point[0], point[2]), (following[0], following[2])
                direction = b[0] - a[0], b[1] - a[1]
                passed = (current[0] - a[0]) * direction[0] + (current[1] - a[1]) * direction[1] >= 0
                reached = passed and point_segment_distance(current, a, b) <= policy.COORD_NAV_LEG_REACH_RADIUS \
                    and self.obstacles.clear(current, b, escape=True)
            if reached:
                del self.path[:index + 1]
                break
        if self.path and not self.obstacles.clear((position[0], position[2]), (self.path[0][0], self.path[0][2]), escape=True):
            self.path = []
        if not self.path:
            if now < self.retry_at:
                raise NavigationWait('暂未找到可通行路径，正在重新寻路')
            # Reuse a map only while it covers the entire next search window.
            profiles = [(policy.COORD_NAV_SEARCH_RADIUS, policy.COORD_NAV_GRID_STEP,
                         policy.COORD_NAV_MAX_NODES, policy.COORD_NAV_HORIZON),
                        (policy.COORD_NAV_FINE_SEARCH_RADIUS, policy.COORD_NAV_FINE_GRID_STEP,
                         policy.COORD_NAV_MAX_NODES, policy.COORD_NAV_FINE_SEARCH_RADIUS),
                        (policy.COORD_NAV_WIDE_SEARCH_RADIUS, policy.COORD_NAV_WIDE_GRID_STEP,
                         policy.COORD_NAV_WIDE_MAX_NODES, policy.COORD_NAV_WIDE_HORIZON)]
            for radius, step, nodes, horizon in profiles:
                check()
                if not self.obstacles.covers_search(position, radius):
                    self.obstacles = self._build_map(self.scene, position, approach_goal, radius)
                self.stats['plans'] += 1
                try:
                    self.path = plan_path(position, target, self.obstacles, check, grid_step=step,
                                          search_radius=radius, max_nodes=nodes, horizon=horizon,
                                          approach_goal=approach_goal, before_search=before_plan)
                except NavigationWait:
                    continue
                self.search_radius = radius
                break
            else:
                self.retry_at = now + policy.COORD_NAV_RETRY_SECONDS
                raise NavigationWait('暂未找到可通行路径，正在重新寻路')
            self.retry_at = 0.
            self.last_decision = {'kind':'alternative_approach' if self.risks.count else 'normal_plan',
                                  'risk_count':self.risks.count, 'risk_revision':self.risks.revision,
                                  'reference_revision': self.reference_revision,
                                  'reference_triangles': len(self.obstacles.reference_risks.entries)}
        # Follow a checked chord through temporary legs; the saved collection
        # destination and its precise arrival radius are unchanged.
        selected = self.path[0]
        prefix = [position,self.path[0]]
        for point in self.path[1:]:
            if distance(position, point) > policy.COORD_NAV_STEERING_LOOKAHEAD:
                break
            if not self.obstacles.clear(current, (point[0], point[2]), escape=True):
                break
            prefix.append(point)
            if self.obstacles.travel_cost(current,(point[0],point[2])) > sum(
                    self.obstacles.travel_cost((a[0],a[2]),(b[0],b[2])) for a,b in zip(prefix,prefix[1:]))+1e-8:
                break
            selected = point
        if self.risks.repeats_failed_entry(current, (selected[0], selected[2]), position[1]):
            self.invalidate()
            self.retry_at = now + policy.COORD_NAV_RETRY_SECONDS
            raise NavigationWait('新路径仍从重复失败方向接近，退开后更换入口', reason='no_path')
        return selected
