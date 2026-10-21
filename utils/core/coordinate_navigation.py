"""Local XZ navigation over partial scene geometry; never treats it as a NavMesh.

No game calls or input here. Exact segment clearance protects A* edges and
shortcuts. The follower supplies interruption checks and releases input before
expensive searches. Missing geometry means wait, rather than silently driving straight.
"""
import heapq
import itertools
import math
import time
from copy import deepcopy

from utils.core import thresholds as policy
from utils.core.coordinate_route import direction_keys, distance, key_vector, valid_position
from utils.core.navigation_water import NAVIGATION_WATER_AREAS as WATER_AREAS


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
    for box in scene.get('colliders', ()):
        heights, polygon = box.get('height'), box.get('polygon')
        if not isinstance(heights, (list, tuple)) or len(heights) != 2 \
                or not all(type(v) in (int, float) and math.isfinite(v) for v in heights) \
                or not isinstance(polygon, (list, tuple)) or len(polygon) < 3 \
                or any(not isinstance(p, (list, tuple)) or len(p) != 2
                       or not all(type(v) in (int, float) and math.isfinite(v) for v in p) for p in polygon):
            continue
        result.add((tuple(heights), tuple(tuple(p) for p in polygon), box.get('category') == 'mountain'))
    return frozenset(result)


class ObstacleMap:
    def __init__(self, scene, position, probes=(), water_areas=WATER_AREAS, goal=None, search_radius=None):
        self.height = position[1]
        self.origin = tuple(position)
        self.search_radius = policy.COORD_NAV_SEARCH_RADIUS if search_radius is None else search_radius
        self.radius = self.search_radius + policy.COORD_NAV_LEG_LENGTH
        self.polygons, self.bins = [], {}
        self.approachable, self.terminal_padding, self.goal = set(), {}, None
        candidates = []
        for box in scene.get('colliders', []):
            heights = box.get('height')
            if not isinstance(heights, (list, tuple)) or len(heights) != 2 \
                    or not all(type(v) in (float, int) and math.isfinite(v) for v in heights):
                continue
            # Ground and overhead geometry must not block the entire XZ map.
            if heights[1] <= position[1] + policy.COORD_NAV_GROUND_CLEARANCE \
                    or heights[0] >= position[1] + policy.COORD_NAV_BODY_HEIGHT:
                continue
            candidates.append((box.get('polygon'), policy.COORD_NAV_CLEARANCE, box.get('category') != 'mountain'))
        candidates.extend((polygon, policy.COORD_NAV_WATER_CLEARANCE, False) for polygon in water_areas)
        candidates.extend((polygon, policy.COORD_NAV_CLEARANCE / 2, False) for polygon in probes)
        for polygon, clearance, approachable in candidates:
            if not isinstance(polygon, (list, tuple)) or len(polygon) < 3 \
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


def plan_path(position, target, obstacles, check=lambda: None, *, grid_step=None, search_radius=None,
              max_nodes=None, horizon=None, approach_goal=None, before_search=lambda: None,
              diagnostics=None, allow_partial=True, any_angle=False):
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
    diagnostics = {} if diagnostics is None else diagnostics
    diagnostics.update(status='complete', target_reached=remaining <= horizon, expanded=0)
    if remaining <= horizon and not obstacles.clear(goal, goal):
        raise NavigationWait('目标附近暂不可通行，正在重新寻路')
    local = goal if remaining <= horizon else tuple(
        a + (b - a) * horizon / remaining for a, b in zip(start, goal))
    check()
    if obstacles.clear(start, local, escape=True):
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
        best, reached = (0, 0), None
        point_cache, edge_cache = {}, {}
        offsets = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]

        def free(node):
            if node not in point_cache:
                point_cache[node] = obstacles.clear(world(node), world(node))
            return point_cache[node]

        while frontier and len(closed) < max_nodes:
            if len(closed) % 32 == 0:
                diagnostics['expanded'] = len(closed)
                check()
            _, _, node, cost = heapq.heappop(frontier)
            if node in closed or cost > costs[node]:
                continue
            closed.add(node)
            point = world(node)
            if math.dist(point, goal) < math.dist(world(best), goal):
                best = node
            if math.dist(point, local) <= step and obstacles.clear(point, local):
                reached = node
                break
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
                new_cost = cost + step * math.hypot(dx, dz)
                parent = node
                # Basic Theta* idea: retain a visible ancestor rather than
                # forcing every guide edge to follow the grid headings.
                # Exact polygon clearance applies to this chord as well.
                ancestor = parents.get(node)
                if any_angle and ancestor is not None:
                    chord = (ancestor, neighbor)
                    if chord not in edge_cache:
                        edge_cache[chord] = obstacles.clear(world(ancestor), world(neighbor),
                                                           escape=ancestor == (0, 0))
                    alternative = costs[ancestor] + math.dist(world(ancestor), world(neighbor))
                    if edge_cache[chord] and alternative < new_cost:
                        parent, new_cost = ancestor, alternative
                if new_cost >= costs.get(neighbor, math.inf):
                    continue
                costs[neighbor], parents[neighbor] = new_cost, parent
                heapq.heappush(frontier, (new_cost + math.dist(world(neighbor), local), next(counter), neighbor, new_cost))
        if reached is None:
            budget_exhausted = bool(frontier) and len(closed) >= max_nodes
            if not allow_partial or remaining <= horizon \
                    or remaining - math.dist(world(best), goal) < policy.COORD_NAV_MIN_PROGRESS:
                diagnostics.update(status='budget' if budget_exhausted else 'no_path', expanded=len(closed))
                raise NavigationWait('寻路计算预算已用完，稍后继续规划' if budget_exhausted
                                     else '暂未找到可通行路径，正在重新寻路',
                                     reason='budget' if budget_exhausted else 'no_path')
            diagnostics.update(status='partial', target_reached=False)
            reached = best
        diagnostics['expanded'] = len(closed)
        nodes = [reached]
        while nodes[-1] != (0, 0):
            nodes.append(parents[nodes[-1]])
        raw = [world(node) for node in reversed(nodes)]
        if obstacles.clear(raw[-1], local):
            raw.append(local)
    # Greedy visible shortcuts, followed by short fixed steering legs. No
    # untested smoothing curve can cut a corner or pass through a thin wall.
    result, index = [], 0
    while index < len(raw) - 1:
        check()
        end = index + 1
        for candidate in range(index + 2, len(raw)):
            if obstacles.clear(raw[index], raw[candidate], escape=index == 0):
                end = candidate
        length = math.dist(raw[index], raw[end])
        count = max(1, math.ceil(length / policy.COORD_NAV_LEG_LENGTH))
        for part in range(1, count + 1):
            x, z = (a + (b - a) * part / count for a, b in zip(raw[index], raw[end]))
            result.append((x, position[1], z))
        index = end
    return result


class LocalNavigator:
    def __init__(self, scene_provider, clock=time.monotonic, water_areas=WATER_AREAS, path_memory=None,
                 planning_clock=time.perf_counter):
        self.scene_provider, self.clock, self.water_areas = scene_provider, clock, water_areas
        self.planning_clock = planning_clock
        self.scene, self.obstacles, self.goal = None, None, None
        self.scene_signature = None
        self.path, self.probes = [], []
        self.retry_at = 0.
        self.retry_reason = 'no_path'
        self.path_memory = path_memory
        self.waypoint = None
        self.memory_leg = None
        self.learning_time = None
        self.learning_started = None
        self.learning_finished = None
        self.memory_path, self.walked_path = [], []
        self.memory_aim = None
        self.memory_loaded, self.memory_failed = False, False
        self.memory_state = 'learning' if path_memory else 'off'
        self.search_radius = policy.COORD_NAV_SEARCH_RADIUS
        self.stats = {'map_builds': 0, 'plans': 0}
        self.water_guard = None
        self.water_guard_context = None
        self.scene_context = None
        self.guide_path, self.guide_goal = [], None
        self.guide_retry_at = 0.
        self.guide_state = 'off'
        self.guide_stats = {'plans': 0, 'expanded': 0}

    def set_waypoint(self, index):
        if self.waypoint != index:
            self.waypoint = index
            self.memory_leg = None
            self.search_radius = policy.COORD_NAV_SEARCH_RADIUS
            self.guide_path, self.guide_goal = [], None
            self.guide_retry_at = 0.

    def arrived(self, position):
        if not self.path_memory or self.waypoint is None or self.memory_failed:
            return
        self._observe_walk(position)
        if not self.memory_failed:
            if self.walked_path and self.walked_path[-1] != tuple(position):
                self.walked_path.append(tuple(position))
            try:
                if self.path_memory.remember(self.waypoint, self.walked_path,
                        (self.learning_finished - self.learning_started)
                        if self.learning_finished is not None and self.learning_started is not None else None):
                    self.memory_state = 'saved'
            except (OSError, ValueError):
                self.memory_state = 'write_error'
        self.walked_path = []
        self.memory_leg = None

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
            return obstacles.clear(start, end, escape=True), sum(a * b for a, b in zip(delta, unit))

        if candidate(preferred)[0]:
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

    def blocked(self, position, target):
        if self.path_memory:
            self.path_memory.reject_selected()
        self.memory_path = []
        self.memory_aim = None
        self.memory_state = 'learning' if self.path_memory else 'off'
        length = distance(position, target)
        if length > policy.COORD_REACH_RADIUS:
            center = (position[0] + (target[0] - position[0]) / length,
                      position[2] + (target[2] - position[2]) / length)
            polygon = tuple((center[0] + .45 * math.cos(i * math.pi / 4),
                             center[1] + .45 * math.sin(i * math.pi / 4)) for i in range(8))
            self.probes.append((self.clock() + policy.COORD_NAV_PROBE_SECONDS, polygon))
            self.probes = self.probes[-32:]
        self.invalidate()

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
            self.memory_aim = None
            self.memory_state = 'learning'
        self._observe_walk(position)
        if not self.memory_loaded:
            self.memory_loaded = True
            if not self.memory_failed:
                self.memory_path = self.path_memory.lookup(self.waypoint, position)
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
                if self.obstacles.clear((position[0], position[2]), (via[0], via[2]), escape=True):
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
            selected = point
        return selected

    def _build_map(self, scene, position, target, radius):
        self.stats['map_builds'] += 1
        return ObstacleMap(scene, position, [p for _, p in self.probes], self.water_areas,
                           goal=target, search_radius=radius)

    def _refresh_obstacles(self, position, target):
        scene = self.scene_provider()
        if not scene or scene.get('state') != 'ready':
            self.invalidate()
            raise NavigationWait('等待最新场景数据，稍后自动寻路', reason='scene')
        # Optional resource/terrain scans can temporarily be unavailable while
        # the native scene remains the same. Prefer its stable identity, not
        # those availability transitions, when deciding whether we changed world.
        context = (('scene', scene['cache_context']) if scene.get('cache_context') is not None else
                   ('entities', scene['global_special_context']) if scene.get('global_special_context') is not None
                   else ('unknown',))
        if self.scene_context is not None and context != self.scene_context:
            # Runtime probes and guidance belong to one game world only.
            self.probes, self.guide_path = [], []
            self.guide_goal, self.guide_retry_at, self.guide_state = None, 0., 'off'
            self.water_guard = None
            self.memory_path, self.memory_aim = [], None
            self.memory_loaded = False
            self.invalidate()
        self.scene_context = deepcopy(context)
        now = self.clock()
        current = (position[0], position[2])
        probes = [probe for probe in self.probes if probe[0] > now
                  and polygon_distance(current, current, probe[1]) > 1e-6]
        if len(probes) != len(self.probes):
            self.obstacles = None
        self.probes = probes
        signature = scene_geometry_signature(scene) if scene is not self.scene else self.scene_signature
        changed = signature != self.scene_signature
        self.scene, self.scene_signature = scene, signature
        if changed or self.obstacles is None \
                or abs(position[1] - self.obstacles.height) >= policy.COORD_NAV_HEIGHT_REFRESH:
            self.obstacles = self._build_map(scene, position, target, max(policy.COORD_NAV_SEARCH_RADIUS, self.search_radius))
            previous = (position[0], position[2])
            for index, point in enumerate(self.path):
                following = (point[0], point[2])
                if not self.obstacles.clear(previous, following, escape=index == 0):
                    self.path = []
                    break
                previous = following
            self.retry_at = 0.
        self.obstacles.set_goal(target)
        return now

    def _guide_target(self, position):
        """Consume the checked corridor in order, without cutting a hidden bend."""
        # One local segment may pass several guide points. Inspect the whole
        # reached prefix, otherwise the next segment can aim back at its start.
        for index in range(len(self.guide_path) - 1, -1, -1):
            point = self.guide_path[index]
            reached = distance(position, point) <= policy.COORD_NAV_LEG_REACH_RADIUS
            if not reached and index + 1 < len(self.guide_path):
                following = self.guide_path[index + 1]
                a, b, current = (point[0], point[2]), (following[0], following[2]), (position[0], position[2])
                passed = sum((p - x) * (y - x) for p, x, y in zip(current, a, b)) >= 0
                reached = passed and point_segment_distance(current, a, b) <= policy.COORD_NAV_LEG_REACH_RADIUS \
                    and self.obstacles.clear(current, b, escape=True)
            if reached:
                del self.guide_path[:index + 1]
                break
        if not self.guide_path:
            return None
        selected = previous = self.guide_path[0]
        length = distance(position, selected)
        for point in self.guide_path[1:]:
            length += distance(previous, point)
            if length > policy.COORD_NAV_HORIZON:
                break
            selected = previous = point
        return selected

    def _plan_guide(self, position, target, approach_goal, check, before_plan):
        """A bounded known-geometry guide, not proof of an unloaded corridor."""
        self.guide_retry_at = self.clock() + policy.COORD_NAV_GUIDE_RETRY_SECONDS
        self.guide_state = 'planning'
        span = max(abs(position[0] - target[0]), abs(position[2] - target[2]))
        radius = min(policy.COORD_NAV_GUIDE_MAX_RADIUS, span + policy.COORD_NAV_GUIDE_MARGIN)
        if span > radius:
            self.guide_state = 'budget'
            return False
        deadline = self.planning_clock() + policy.COORD_NAV_GUIDE_SECONDS
        def guarded():
            check()
            if self.planning_clock() > deadline:
                raise NavigationWait('大范围路线计算预算已用完，稍后继续规划', reason='budget')
        before_plan()  # Release movement before map building as well as search.
        guarded()
        obstacles = ObstacleMap(self.scene, position, [p for _, p in self.probes], self.water_areas,
                                goal=approach_goal, search_radius=radius)
        for step in (policy.COORD_NAV_GUIDE_GRID_STEP, policy.COORD_NAV_GUIDE_FINE_GRID_STEP):
            guarded()
            details = {}
            self.guide_stats['plans'] += 1
            try:
                path = plan_path(position, target, obstacles, guarded, grid_step=step,
                                 search_radius=radius, max_nodes=policy.COORD_NAV_GUIDE_MAX_NODES,
                                 horizon=distance(position, target) + 1., approach_goal=approach_goal,
                                 allow_partial=False, any_angle=True, diagnostics=details)
            except NavigationWait as exc:
                self.guide_state = exc.reason
                self.guide_stats['expanded'] += details.get('expanded', 0)
                if self.planning_clock() > deadline:
                    break
                continue
            self.guide_path, self.guide_goal, self.guide_state = path, tuple(target), 'ready'
            self.guide_stats['expanded'] += details['expanded']
            return True
        return False

    def _plan_local(self, position, target, approach_goal, check, before_plan):
        """Prefer a completed local segment; keep partial progress as fallback."""
        profiles = [(policy.COORD_NAV_SEARCH_RADIUS, policy.COORD_NAV_GRID_STEP,
                     policy.COORD_NAV_MAX_NODES, policy.COORD_NAV_HORIZON),
                    (policy.COORD_NAV_FINE_SEARCH_RADIUS, policy.COORD_NAV_FINE_GRID_STEP,
                     policy.COORD_NAV_MAX_NODES, policy.COORD_NAV_FINE_SEARCH_RADIUS),
                    (policy.COORD_NAV_WIDE_SEARCH_RADIUS, policy.COORD_NAV_WIDE_GRID_STEP,
                     policy.COORD_NAV_WIDE_MAX_NODES, policy.COORD_NAV_WIDE_HORIZON)]
        partial, failure = None, 'no_path'
        for radius, step, nodes, horizon in profiles:
            check()
            if not self.obstacles.covers_search(position, radius):
                self.obstacles = self._build_map(self.scene, position, approach_goal, radius)
            self.stats['plans'] += 1
            details = {}
            try:
                path = plan_path(position, target, self.obstacles, check, grid_step=step,
                                 search_radius=radius, max_nodes=nodes, horizon=horizon,
                                 approach_goal=approach_goal, before_search=before_plan, diagnostics=details)
            except NavigationWait as exc:
                failure = exc.reason
                continue
            result = path, self.obstacles, radius
            if details['status'] == 'complete':
                return result, partial, failure
            if partial is None or distance(path[-1], target) < distance(partial[0][-1], target):
                partial = result
        return None, partial, failure

    def _next_target(self, position, target, check=lambda: None, before_plan=lambda: None, approach_goal=None):
        approach_goal = target if approach_goal is None else approach_goal
        now = self._refresh_obstacles(position, approach_goal)
        current = (position[0], position[2])
        if self.goal != tuple(target):
            self.path, self.goal, self.retry_at = [], tuple(target), 0.
            self.obstacles.set_goal(approach_goal)
        if self.guide_goal != tuple(target):
            self.guide_path, self.guide_goal = [], tuple(target)
            self.guide_retry_at, self.guide_state = 0., 'off'
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
                raise NavigationWait('寻路计算预算已用完，稍后继续规划' if self.retry_reason == 'budget'
                                     else '暂未找到可通行路径，正在重新寻路', reason=self.retry_reason)
            via = self._guide_target(position) or target
            complete, partial, failure = self._plan_local(position, via, approach_goal, check, before_plan)
            if complete is None and now >= self.guide_retry_at:
                try:
                    guided = self._plan_guide(position, target, approach_goal, check, before_plan)
                except NavigationWait as exc:
                    if exc.reason != 'budget':
                        raise
                    self.guide_state, guided = 'budget', False
                if guided:
                    via = self._guide_target(position) or target
                    complete, guided_partial, failure = self._plan_local(
                        position, via, approach_goal, check, before_plan)
                    partial = guided_partial or partial
                elif self.guide_state == 'budget':
                    failure = 'budget'
            chosen = complete or partial
            if chosen is None:
                if self.guide_state == 'budget':
                    failure = 'budget'
                self.retry_reason = failure
                self.retry_at = self.clock() + policy.COORD_NAV_RETRY_SECONDS
                raise NavigationWait('寻路计算预算已用完，稍后继续规划' if failure == 'budget'
                                     else '暂未找到可通行路径，正在重新寻路', reason=failure)
            self.path, self.obstacles, self.search_radius = chosen
            self.retry_at = 0.
        # Follow a checked chord through temporary legs; the saved collection
        # destination and its precise arrival radius are unchanged.
        selected = self.path[0]
        for point in self.path[1:]:
            if distance(position, point) > policy.COORD_NAV_STEERING_LOOKAHEAD:
                break
            if not self.obstacles.clear(current, (point[0], point[2]), escape=True):
                break
            selected = point
        return selected
