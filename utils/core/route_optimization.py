"""Bounded, deterministic visit ordering over an explicitly partial scene.

Costs are estimates, never permission to move. The live navigator must validate
each segment again. No solver dependency, game input or persistent path memory.
"""
from copy import deepcopy
import math
import itertools
import time

from utils.core import thresholds as policy
from utils.core.coordinate_points import validate_points, validate_loop_count
from utils.core.coordinate_route import distance, valid_position
from utils.core.coordinate_navigation import ObstacleMap, NavigationWait, plan_path, WATER_AREAS


class _SearchBudget(Exception):
    pass


def route_cost(order, matrix, loops=1):
    """Matrix node 0 is the actual start; point i uses node i+1."""
    if not order:
        return 0.
    legs = sum(matrix[a+1][b+1] for a, b in zip(order, order[1:]))
    return matrix[0][order[0]+1] + loops * legs + (loops-1) * matrix[order[-1]+1][order[0]+1]


def optimize_order(matrix, loops=1, check=lambda: None):
    """Nearest-neighbor seed plus bounded 2-opt; retain original if no gain.

    Directed costs are supported: reversing a segment changes its internal
    edges too. This is a heuristic, with no shortest-tour guarantee.
    """
    validate_loop_count(loops)
    n = len(matrix)-1
    if n < 1 or any(len(row) != n+1 for row in matrix) or any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0 for row in matrix for v in row):
        raise ValueError('路线代价矩阵无效。')
    original = list(range(n))
    if n <= policy.COORD_ROUTE_EXACT_POINTS:
        best, score = original, route_cost(original, matrix, loops)
        for candidate in itertools.permutations(original):
            check()
            value = route_cost(candidate, matrix, loops)
            if value < score - 1e-7:
                best, score = list(candidate), value
        return best
    remaining, greedy, last = set(original), [], 0
    while remaining:
        check()
        chosen = min(remaining, key=lambda i: (matrix[last][i+1], i))
        greedy.append(chosen)
        remaining.remove(chosen)
        last = chosen+1
    best = min((original, greedy), key=lambda order: route_cost(order, matrix, loops))[:]
    score = route_cost(best, matrix, loops)
    for _ in range(policy.COORD_ROUTE_OPT_PASSES):
        improved = False
        for a in range(n-1):
            for b in range(a+1, n):
                check()
                candidate = best[:a] + best[a:b+1][::-1] + best[b+1:]
                value = route_cost(candidate, matrix, loops)
                if value < score - 1e-7:
                    best, score, improved = candidate, value, True
        if not improved:
            break
    return best


def optimize_route(data, position, scene, loops=1, *, water_areas=WATER_AREAS,
                   clock=time.perf_counter, check=lambda: None):
    validate_points(data)
    validate_loop_count(loops)
    if not valid_position(position) or not data['points']:
        raise ValueError('请连接游戏并保存至少一个点位后再优化。')
    if 'special_patrol' in data or any('special_resource' in p for p in data['points']):
        raise ValueError('动态资源使用实时巡查，不对其保存固定点位优化路线。')
    if not isinstance(scene, dict) or scene.get('state') != 'ready':
        raise ValueError('实时场景尚未就绪，请等待地图连接后再优化。')
    if len(data['points']) > policy.COORD_ROUTE_OPT_MAX_POINTS:
        raise ValueError(f'单次优化最多支持 {policy.COORD_ROUTE_OPT_MAX_POINTS} 个点，请拆分路线。')
    positions = [tuple(position)] + [tuple(p['position']) for p in data['points']]
    deadline = clock() + policy.COORD_ROUTE_OPT_SECONDS
    def guard():
        check()
        if clock() >= deadline:
            raise _SearchBudget()
    # Unknown edges receive a conservative ranking cost, never a false route.
    # Finish the whole matrix before ranking so evaluations use fixed costs.
    scale = max(1., max(distance(a, b) for a in positions for b in positions))
    matrix = [[0. if a == b else distance(a, b) + scale * policy.COORD_ROUTE_UNKNOWN_PENALTY
               for b in positions] for a in positions]
    estimated, proved, budget = set(), set(), False
    for i, a in enumerate(positions):
        for j, b in enumerate(positions):
            if i == j:
                continue
            estimated.add((i, j))
    try:
        pending = {}
        # Complete cheap visible-edge checks first. One difficult pair must not
        # consume the budget before all the other obvious edges are measured.
        # Keep one indexed map at a time; never allocate N squared maps.
        for i, a in enumerate(positions):
            guard()
            obstacles = ObstacleMap(scene, a, water_areas=water_areas,
                                    search_radius=policy.COORD_NAV_GUIDE_MAX_RADIUS)
            for j, b in enumerate(positions):
                if i == j:
                    continue
                guard()
                # A differing floor is not certified by this XZ projection.
                if abs(a[1]-b[1]) > policy.COORD_MAX_HEIGHT_ERROR:
                    continue
                obstacles.set_goal(b)
                if obstacles.clear((a[0], a[2]), (b[0], b[2]), escape=True):
                    matrix[i][j] = distance(a, b)
                    estimated.discard((i, j))
                    proved.add((i, j))
                else:
                    pending.setdefault(i, []).append(j)
        for i, destinations in pending.items():
            guard()
            a = positions[i]
            obstacles = ObstacleMap(scene, a, water_areas=water_areas,
                                    search_radius=policy.COORD_NAV_GUIDE_MAX_RADIUS)
            for j in destinations:
                guard()
                b = positions[j]
                obstacles.set_goal(b)
                radius = min(policy.COORD_NAV_GUIDE_MAX_RADIUS,
                             max(abs(a[0]-b[0]), abs(a[2]-b[2])) + policy.COORD_NAV_GUIDE_MARGIN)
                try:
                    path = plan_path(a, b, obstacles, check=guard,
                        grid_step=policy.COORD_NAV_GUIDE_GRID_STEP, search_radius=radius,
                        max_nodes=policy.COORD_NAV_GUIDE_MAX_NODES, horizon=distance(a, b)+1,
                        approach_goal=b, allow_partial=False, any_angle=True)
                except NavigationWait:
                    continue
                if path and distance(path[-1], b) <= .001:
                    full_path = [a, *path]
                    matrix[i][j] = sum(distance(u, v) for u, v in zip(full_path, full_path[1:]))
                    estimated.discard((i, j))
                    proved.add((i, j))
    except _SearchBudget:
        budget = True
    # Ranking gets its own small budget; keep the original order on expiry.
    ranking_end = clock() + policy.COORD_ROUTE_ORDER_SECONDS
    def rank_guard():
        check()
        if clock() >= ranking_end:
            raise _SearchBudget()
    original = list(range(len(data['points'])))
    try:
        order = optimize_order(matrix, loops, rank_guard)
    except _SearchBudget:
        order, budget = original, True
    result = deepcopy(data)
    result['points'] = [deepcopy(data['points'][i]) for i in order]
    for i, p in enumerate(result['points'], 1):
        p['id'] = i
    before, after = route_cost(original, matrix, loops), route_cost(order, matrix, loops)
    result['route_optimization'] = {'version': 1, 'start_position': list(position), 'loop_count': loops,
        'source_point_ids': [data['points'][i]['id'] for i in order],
        'estimated_cost_before': before, 'estimated_cost_after': after,
        'unknown_edges': len(estimated), 'checked_edges': len(proved), 'budget_reached': budget,
        'cost_units': 'distance_with_unknown_penalty', 'partial_geometry': True}
    return validate_points(result)
