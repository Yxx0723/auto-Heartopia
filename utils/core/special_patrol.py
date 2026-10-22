"""Daily-resource search plans and strict target selection, with no I/O.

Fixed definitions suggest areas to visit; they do not establish a passable road
or a daily spawn. Every movement still goes through the live navigator.
"""
import math
from copy import deepcopy
from utils.core.coordinate_route import valid_position
from utils.core.special_approach import global_special_targets, special_search_targets

SPECIAL_IDS = {'roaming_oak': 42001, 'fluorite': 42002}


class PatrolLegBlocked(ValueError):
    """An ephemeral search leg can be skipped without pausing saved routes."""


def patrol_checkpoints(scene):
    if scene.get('house_entrance_state') != 'ready':
        return []
    houses = {}
    for obj in scene.get('house_entrances', ()):
        number, position = obj.get('house_number'), obj.get('position')
        if type(number) is not int or not 1 <= number <= 12 \
                or obj.get('source') != 'main-map-house-player-position' or not valid_position(position) \
                or number in houses:
            return []
        houses[number] = list(position)
    return [houses[i] for i in range(1, 13)] if len(houses) == 12 else []


def patrol_target(scene, category, rejected, allow_hints=True):
    if scene.get('state') != 'ready':
        return 'wait', None
    static_id = SPECIAL_IDS[category]
    live = [o for o in scene.get('objects', ()) if o.get('category') == category
            and o.get('resource_static_id') == static_id and o.get('map_dynamic') is True
            and not o.get('map_cached') and valid_position(o.get('position'))]
    if len(live) > 1:
        return 'wait', None
    if live:
        obj = live[0]
        if type(obj.get('resource_available')) is not int:
            return 'wait', None
        return ('live' if obj['resource_available'] > 0 else 'unavailable'), obj
    if not allow_hints:
        return 'search', None
    if scene.get('global_special_state') != 'ready':
        return 'wait', None
    for targets in (global_special_targets(scene, category, static_id),
                    special_search_targets(scene, category, static_id)):
        fresh = [o for o in targets if not any(math.dist(o['position'], p) <= 2. for p in rejected)]
        if len(fresh) > 1:
            return 'wait', None
        if fresh:
            return 'hint', fresh[0]
    return 'search', None


def make_patrol_data(scene, position, category, name, count, stamp):
    if category not in SPECIAL_IDS or type(count) is not int or not 1 <= count <= 100:
        raise ValueError('动态巡查目标或连按次数无效')
    if scene.get('state') != 'ready' or not valid_position(position):
        raise ValueError('场景尚未读取完成，请稍后再启动巡查')
    checkpoints = patrol_checkpoints(scene)
    state, target = patrol_target(scene, category, [], allow_hints=False)
    if not checkpoints and target is None:
        raise ValueError('1～12号房门入口尚未准备好，请等待主地图加载后重试')
    identity = {'category': category, 'static_id': SPECIAL_IDS[category], 'press_count': count}
    return {'schema': 'coordinate-points-v1',
            'special_patrol': {**identity, 'checkpoints': checkpoints,
                               'checkpoint_kind': 'house-entrances',
                               'checkpoint_houses': list(range(1, 13)) if checkpoints else []},
            'points': [{'id': 1, 'name': name,
                        'position': list(target['position'] if target is not None else position),
                        'sample_at': stamp, 'saved_at': stamp, 'special_resource': deepcopy(identity)}]}
