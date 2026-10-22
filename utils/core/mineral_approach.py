"""Explicit definition-centred mineral points use nearby standing positions.

Manual foot positions retain their exact arrival rule. Definition references
never count as loaded entities or authorize collection on their own.
"""
import math

from utils.core import thresholds as policy
from utils.core.coordinate_route import distance, valid_position


def mineral_identity(point):
    source = point.get('source')
    if isinstance(source, dict) and source.get('kind') == 'rare_wood' \
            and source.get('item_type') == 211 and type(source.get('item_type')) is int \
            and source.get('source') == 'main-map-resource-definition' \
            and source.get('stand_position') == 'resource-center' \
            and type(source.get('resource_id')) is int and source['resource_id'] > 0 \
            and valid_position(source.get('resource_position')) \
            and type(source.get('height_offset')) in (int, float) \
            and math.isfinite(source['height_offset']) and abs(source['height_offset']) <= 2:
        return {'category': 'tree', 'resource_id': source['resource_id'],
                'position': tuple(source['resource_position']), 'height_offset': source['height_offset']}
    if not isinstance(source, dict) or source.get('source') != 'main-map-resource-definition' \
            or source.get('stand_position') != 'resource-center' \
            or source.get('kind') not in ('ore', 'rock') \
            or type(source.get('resource_id')) is not int or source['resource_id'] <= 0 \
            or type(source.get('item_type')) is not int \
            or source['item_type'] != {'ore': 302, 'rock': 301}[source['kind']]:
        return None
    center = point.get('_mineral_center', point.get('position'))
    if not valid_position(center):
        return None
    return {'category': source['kind'], 'resource_id': source['resource_id'], 'position': tuple(center)}


def loaded_mineral(scene, identity):
    if scene.get('state') != 'ready' or scene.get('world_state') != 'ready':
        return None
    matches = [obj for obj in scene.get('objects', ())
               if obj.get('category') == identity['category'] and obj.get('map_static') is True
               and obj.get('kind') in ('entity', 'object') and not obj.get('map_cached')
               and not obj.get('map_definition') and not obj.get('resource_global_only')
               and (identity['category'] != 'tree' or (obj.get('resource_item_type') == 211
                    and obj.get('resource_id') == identity['resource_id']
                    and type(obj.get('resource_available')) is int))
               and valid_position(obj.get('position'))
               and math.dist(obj['position'], identity['position']) <= policy.COORD_MINERAL_IDENTITY_TOLERANCE]
    return matches[0] if len(matches) == 1 else None


def mineral_near(position, center):
    return distance(position, center) <= policy.COORD_MINERAL_APPROACH_RADIUS \
        and abs(position[1]-center[1]) <= policy.COORD_MAX_HEIGHT_ERROR


def resource_near(position, center, identity):
    if identity['category'] == 'tree':
        return distance(position, center) <= 1.25 and abs(position[1]-center[1]) <= 2.
    return mineral_near(position, center)


def mineral_standpoint(position, center, obstacles, avoid=(), *, tree_height=None):
    angle = math.atan2(position[2]-center[2], position[0]-center[0])
    candidates = []
    radii = ((1., 1.1, 1.2) if tree_height is not None else
             (policy.COORD_MINERAL_APPROACH_INNER_RADIUS,
              policy.COORD_MINERAL_APPROACH_MIDDLE_RADIUS, policy.COORD_MINERAL_APPROACH_RADIUS))
    for radius in radii:
        for turn in range(24):
            theta = angle + turn*math.tau/24
            point = [center[0]+radius*math.cos(theta), center[1]+(tree_height or 0.), center[2]+radius*math.sin(theta)]
            obstacles.set_goal(point)
            if obstacles.clear((point[0], point[2]), (point[0], point[2])) \
                    and all(distance(point, seen) > policy.COORD_SPECIAL_STANDPOINT_REUSE for seen in avoid):
                candidates.append(point)
    return min(candidates, key=lambda p: distance(position, p)) if candidates else None
