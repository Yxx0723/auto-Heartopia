"""Create a separate rare-timber route from typed, current main-map definitions.

Read-only against the game. No overwrite and no automatic playback. The order
uses the same bounded partial-scene planner as the window's optimize action.
"""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path

from utils.core.coordinate_points import validate_points
from utils.core.coordinate_route import valid_position
from utils.core.mineral_approach import mineral_standpoint
from utils.core.coordinate_navigation import ObstacleMap
from utils.core.route_optimization import optimize_route
from utils.drivers.coordinate_point_routes import PointRoutes
from utils.drivers.mono_scene import MemorySceneMap


def build_route(scene, stamp):
    if scene.get('state') != 'ready' or scene.get('world_state') != 'ready' \
            or scene.get('global_resource_state') != 'ready' \
            or (scene.get('fixed_resource_context') or {}).get('map') != 'main' \
            or not valid_position(scene.get('position')):
        raise ValueError('请进入主地图并等待实时场景和全图树木配置就绪。')
    definitions = [p for p in scene.get('global_resources', []) if p.get('category') == 'tree'
                   and p.get('item_type_id') == 211 and p.get('map_definition') is True
                   and p.get('source') == 'main-map-resource-definition']
    if not definitions or any(not valid_position(p.get('position'))
            or type(p.get('resource_id')) is not int or p['resource_id'] <= 0 for p in definitions) \
            or len({p['resource_id'] for p in definitions}) != len(definitions):
        raise ValueError('珍稀木材树配置缺失或身份不唯一，未创建路线。')
    current = scene['position']
    nearest = min(definitions, key=lambda p: math.dist(current, p['position']))
    live = [p for p in scene.get('objects', []) if p.get('category') == 'tree'
            and p.get('kind') == 'entity' and p.get('map_static') is True
            and not p.get('map_cached') and not p.get('map_definition') and not p.get('resource_global_only')
            and p.get('resource_item_type') == 211 and p.get('resource_id') == nearest['resource_id']
            and type(p.get('resource_available')) is int and valid_position(p.get('position'))
            and math.dist(p['position'], nearest['position']) <= .25]
    if len(live) != 1 or math.hypot(current[0]-nearest['position'][0], current[2]-nearest['position'][2]) > 1.5:
        raise ValueError('请先靠近一棵珍稀木材树站定，核对实时身份及站立高度后再生成。')
    height = current[1]-nearest['position'][1]
    if abs(height) > 2:
        raise ValueError('当前人物与珍稀木材树高度不符。')
    pending = {p['resource_id']: p for p in definitions}
    points, previous = [], current
    while pending:
        resource = min(pending.values(), key=lambda p: (math.dist(previous, p['position']), p['resource_id']))
        center = resource['position']
        if resource['resource_id'] == nearest['resource_id']:
            stand = list(current)
        else:
            obstacles = ObstacleMap(scene, [center[0], center[1]+height, center[2]])
            stand = mineral_standpoint(previous, center, obstacles, tree_height=height)
            if stand is None:
                # A remote point is a route reference; live navigation will
                # find a stand or defer it. Never open a collider/water here.
                theta = math.atan2(previous[2]-center[2], previous[0]-center[0])
                stand = [center[0]+1.1*math.cos(theta), center[1]+height, center[2]+1.1*math.sin(theta)]
        number = len(points)+1
        points.append({'id': number, 'name': f'稀有木材树 {number}', 'position': stand,
                       'sample_at': stamp, 'saved_at': stamp, 'collect_wait': 3., 'collect_count': 3,
                       'source': {'kind': 'rare_wood', 'item_type': 211, 'resource_id': resource['resource_id'],
                                  'resource_position': list(center), 'height_offset': height,
                                  'source': 'main-map-resource-definition', 'stand_position': 'resource-center'}})
        previous = stand
        del pending[resource['resource_id']]
    route = {'schema': 'coordinate-points-v1', 'points': points,
             'planning': {'resource': '珍稀木材', 'material_id': 40004, 'tree_item_type': 211,
                          'resource_count': len(points), 'created_at': stamp,
                          'start_position': list(current), 'partial_geometry': True,
                          'collection_verified': False, 'standpoints_rechecked_live': True}}
    return optimize_route(validate_points(route), current, scene)


def main():
    parser = argparse.ArgumentParser(description='从全图配置创建独立的稀有木材采集路线')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--scene', type=Path, help='使用此前只读导出的同一会话场景')
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[3]/'outputs/点位')
    parser.add_argument('--name', default='稀有木材')
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    if args.scene:
        snapshot = json.loads(args.scene.read_text(encoding='utf-8-sig'))
    else:
        if not args.pid:
            parser.error('请提供 --pid 或 --scene')
        reader = MemorySceneMap(args.pid, Path(__file__).resolve().parents[1]/'resources/monitor/profile.json')
        try:
            snapshot = reader.snapshot()
        finally:
            reader.close()
    data = build_route(snapshot, datetime.now().astimezone().isoformat(timespec='milliseconds'))
    if not args.plan:
        routes = PointRoutes(args.output_dir/'我的点位.json')
        routes.create(args.name, data)
    print(json.dumps({'route': args.name, 'points': len(data['points']), 'written': not args.plan,
                      'file': str(args.output_dir/(args.name+'.json')), 'read_only_game': True,
                      'resource_ids': [p['source']['resource_id'] for p in data['points']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
