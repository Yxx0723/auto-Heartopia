"""Build a mining route from the live main-map ore definitions.

Read-only against the game. Writes outputs/点位/矿石.json through PointStore so
the file keeps the format the monitor itself writes.

The route is sized against the ore respawn: a loop shorter than the respawn
only adds waiting, a loop longer than it adds walking that pays nothing, so the
subset is chosen to maximise ore per real second (yield / max(loop, respawn)).

Usage: python tools/build_ore_route.py [--pid PID] [--name 矿石] [--force] [--plan]
       python tools/build_ore_route.py --include-rock | --all
"""
import argparse
from datetime import datetime
import math
from pathlib import Path

from utils.core.coordinate_points import validate_points
from utils.core.navigation_water import NAVIGATION_WATER_AREAS
from utils.drivers.coordinate_point_store import PointStore
from utils.drivers.mono_scene import MemorySceneMap

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'resources' / 'monitor' / 'profile.json'
DATA_DIR = ROOT.parent.parent / 'outputs'
ORE_ITEM_TYPE = 302
ROCK_ITEM_TYPE = 301
# Public resource tables: ordinary ore (and stone) respawn 5 minutes after being
# mined and yield 3 pieces per node. Not read from game memory; adjust here if a
# future build changes it.
RESPAWN_SECONDS = 300.
YIELD_PER_NODE = 3
# Measured 3.25 u/s on clear straight runs; waypoint-to-waypoint straight lines
# understate the walked path, so plan with a slightly slower figure.
WALK_SPEED = 3.0
COLLECT_SECONDS = 3.0
# Keep one loop inside the respawn window plus a small margin: finishing much
# later than the respawn is walking that the respawn cannot pay back.
LOOP_BUDGET_FACTOR = 1.1
# 250 is the smallest penalty that removed the last lake crossing on the
# 2026-10-21 ore tour; below it 2-opt keeps a straight leg through the water.
WATER_PENALTY = 250.
WATER_SAMPLES = 24


def stamp():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def inside_polygon(x, z, polygon):
    hit = False
    count = len(polygon)
    for index in range(count):
        ax, az = polygon[index]
        bx, bz = polygon[(index + 1) % count]
        if (az > z) != (bz > z):
            cross = (bx - ax) * (z - az) / (bz - az) + ax
            if x < cross:
                hit = not hit
    return hit


def crosses_water(a, b):
    for step in range(1, WATER_SAMPLES + 1):
        ratio = step / (WATER_SAMPLES + 1)
        x = a[0] + (b[0] - a[0]) * ratio
        z = a[2] + (b[2] - a[2]) * ratio
        for polygon in NAVIGATION_WATER_AREAS:
            if inside_polygon(x, z, polygon):
                return True
    return False


def flat_distance(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


def cost_matrix(points):
    """Plain geometry, plus a penalty that only steers the ordering away from lakes."""
    size = len(points)
    matrix = [[0.] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1, size):
            value = flat_distance(points[i], points[j])
            if crosses_water(points[i], points[j]):
                value += WATER_PENALTY
            matrix[i][j] = matrix[j][i] = value
    return matrix


def cycle_geometry(order, points):
    return sum(flat_distance(points[order[i]], points[order[(i + 1) % len(order)]])
               for i in range(len(order)))


def two_opt(order, matrix):
    """Closed-tour 2-opt with O(1) gain tests against the cost matrix."""
    best, size = list(order), len(order)
    if size < 4:
        return best
    improved = True
    while improved:
        improved = False
        for i in range(size - 1):
            for j in range(i + 1, size):
                if i == 0 and j == size - 1:
                    continue  # Reversing the whole cycle changes nothing but looks like a gain.
                before = best[i - 1] if i else best[-1]
                after = best[(j + 1) % size]
                gain = (matrix[before][best[i]] + matrix[best[j]][after]
                        - matrix[before][best[j]] - matrix[best[i]][after])
                if gain > 1e-9:
                    best[i:j + 1] = reversed(best[i:j + 1])
                    improved = True
    return best


def grow_orders(matrix):
    """Cheapest-insertion cycles from every seed, refined at every size."""
    size = len(matrix)
    for seed in range(size):
        order, remaining = [seed], set(range(size)) - {seed}
        while remaining:
            choice = None
            for point in remaining:
                if len(order) == 1:
                    candidates = [(2 * matrix[order[0]][point], 1)]
                else:
                    candidates = [(matrix[order[edge]][point] + matrix[point][order[(edge + 1) % len(order)]]
                                   - matrix[order[edge]][order[(edge + 1) % len(order)]], edge + 1)
                                  for edge in range(len(order))]
                for delta, at in candidates:
                    if choice is None or delta < choice[0]:
                        choice = (delta, point, at)
            order.insert(choice[2], choice[1])
            remaining.discard(choice[1])
            if len(order) >= 3:
                yield two_opt(order, matrix)


def score(order, points):
    geometry = cycle_geometry(order, points)
    seconds = geometry / WALK_SPEED + len(order) * COLLECT_SECONDS
    rate = len(order) * YIELD_PER_NODE / max(seconds, RESPAWN_SECONDS)
    return rate, seconds, geometry


def best_subset(points):
    """Most ore per real second, among loops that stay inside the respawn budget.

    Without the budget the answer is always "mine the whole map", which is more
    efficient per second but a much longer walk than most people want to run.
    """
    matrix = cost_matrix(points)
    budget = RESPAWN_SECONDS * LOOP_BUDGET_FACTOR
    within, fallback = None, None
    for order in grow_orders(matrix):
        rate, seconds, geometry = score(order, points)
        key = (rate, -len(order), -geometry)
        if fallback is None or key > fallback[0]:
            fallback = (key, list(order), seconds, geometry, rate)
        if seconds <= budget and (within is None or key > within[0]):
            within = (key, list(order), seconds, geometry, rate)
    best = within or fallback
    return best[1], best[2], best[3], best[4]


def rotate_to(order, points, start):
    """Start the saved cycle at the ore nearest the player's current position."""
    nearest = min(range(len(order)), key=lambda i: math.dist(start, points[order[i]]))
    return order[nearest:] + order[:nearest]


def build(pid, name, include_rock, force, data_dir, use_all, plan_only):
    scene = MemorySceneMap(pid, PROFILE)
    try:
        snapshot = scene.snapshot()
    finally:
        scene.close()
    definitions = snapshot.get('global_resources') or []
    wanted = {'ore'} | ({'rock'} if include_rock else set())
    picked = [obj for obj in definitions if obj.get('category') in wanted]
    if not picked:
        raise ValueError('当前游戏内没有读到矿石点位，请进入主地图后重试。')
    start = snapshot.get('position') or picked[0]['position']
    points = [list(obj['position']) for obj in picked]
    budget_seconds = RESPAWN_SECONDS * LOOP_BUDGET_FACTOR
    if use_all:
        order = two_opt(list(range(len(points))), cost_matrix(points))
    else:
        order = best_subset(points)[0]
    order = rotate_to(order, points, start)
    rate, seconds, geometry = score(order, points)
    print(f'全图矿石 {len(picked)} 个 · 选中 {len(order)} 个 · 一圈上限 {budget_seconds / 60:.1f} 分钟')
    print(f'一圈 {geometry:.0f} 单位 · 约 {seconds / 60:.1f} 分钟'
          f'（走 {geometry / WALK_SPEED:.0f}s + 采 {len(order) * COLLECT_SECONDS:.0f}s）')
    print(f'刷新 {RESPAWN_SECONDS / 60:.0f} 分钟 · 一圈 {len(order) * YIELD_PER_NODE} 个矿石'
          f' · 折合每小时约 {rate * 3600:.0f} 个'
          + ('（跑完要等刷新）' if seconds < RESPAWN_SECONDS else '（可不停歇循环）'))
    if plan_only:
        return None, None, start
    at = stamp()
    route = []
    for number, index in enumerate(order, 1):
        match = picked[index]
        route.append({'id': number, 'name': f'{name} {number}', 'position': points[index],
                      'sample_at': at, 'saved_at': at, 'collect_wait': COLLECT_SECONDS,
                      'source': {'kind': match['category'], 'item_type': match['item_type_id'],
                                 'resource_id': match['resource_id'],
                                 'source': match.get('source', 'main-map-resource-definition'),
                                 'stand_position': 'resource-center'}})
    data = {'schema': 'coordinate-points-v1', 'points': route,
            'resource_count': len(route), 'source_resource_count': len(definitions),
            'added_count': len(route), 'item_types': sorted({p['source']['item_type'] for p in route}),
            'visit_order': 'cheapest-insertion-plus-2opt-cycle-water-penalty',
            'start_position': list(start), 'updated_at': at,
            'plan': {'respawn_seconds': RESPAWN_SECONDS, 'yield_per_node': YIELD_PER_NODE,
                     'walk_speed': WALK_SPEED, 'collect_seconds': COLLECT_SECONDS,
                     'cycle_units': round(geometry, 1), 'loop_seconds': round(seconds, 1),
                     'ore_per_hour': round(rate * 3600, 1),
                     'idle_wait_seconds': round(max(0., RESPAWN_SECONDS - seconds), 1),
                     'loop_budget_seconds': round(budget_seconds, 1),
                     'ore_per_loop': len(route) * YIELD_PER_NODE,
                     'source_note': '刷新时长与单次产出取自公开资源表，非游戏内存读数'}}
    validate_points(data)
    path = data_dir / '点位' / f'{name}.json'
    if path.exists():
        if not force:
            raise ValueError(f'路线 {name} 已存在，加 --force 覆盖（旧档会先进路线回收站）。')
        backup = data_dir / '点位' / '路线回收站' / f'{name}-建路线前-{datetime.now():%Y%m%d-%H%M%S}.json'
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
        path.unlink()
        print('已备份旧档 ->', backup)
    PointStore(path).create(data)
    return path, data, start


def main():
    parser = argparse.ArgumentParser(description='按矿石刷新时长生成采集路线')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--name', default='矿石')
    parser.add_argument('--include-rock', action='store_true', help='同时纳入石头（301）')
    parser.add_argument('--all', action='store_true', help='不做取舍，全部点位都放进路线')
    parser.add_argument('--plan', action='store_true', help='只算不写')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--data-dir', type=Path, default=DATA_DIR)
    args = parser.parse_args()
    pid = args.pid
    if pid is None:
        from utils.ui.coordinate_monitor import CoordinateMonitor
        status = CoordinateMonitor(PROFILE).status()
        if status['state'] != 'connected':
            raise ValueError('请先进入角色，或用 --pid 指定游戏进程。')
        pid = status['pid']
    path, data, start = build(pid, args.name, args.include_rock, args.force,
                              args.data_dir, args.all, args.plan)
    if path is None:
        return
    print(f'玩家起点 {[round(v, 2) for v in start]}')
    print(f'路线 {path} -> {len(data["points"])} 点')
    print(' '.join('%s(%.0f,%.0f)' % (p['name'], p['position'][0], p['position'][2])
                   for p in data['points']))


if __name__ == '__main__':
    main()
