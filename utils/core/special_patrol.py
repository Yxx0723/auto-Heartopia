"""Pure, scoped house-entrance plans. A checkpoint is not a resource spawn."""
from copy import deepcopy
from utils.core.coordinate_route import valid_position
from utils.core.coordinate_points import validate_points

SPECIAL_IDS = {'roaming_oak': 42001, 'fluorite': 42002}


class SpecialTargetUnavailable(ValueError):
    """A live candidate cannot currently authorize collection."""


def patrol_target(scene, category):
    if scene.get('state') != 'ready':
        return 'wait', None
    if category == 'both':
        # A different species is not duplicate identity. Same-species ambiguity stays unknown.
        for obj in scene.get('objects', ()):
            kind=obj.get('category')
            if kind in SPECIAL_IDS:
                state,target=patrol_target(scene,kind)
                if state=='live':
                    return state,target
        return ('wait',None) if any(patrol_target(scene,k)[0]=='wait' for k in SPECIAL_IDS) else ('search',None)
    live = [o for o in scene.get('objects', ()) if o.get('category') == category
            and o.get('resource_static_id') == SPECIAL_IDS[category]
            and o.get('map_dynamic') is True and not o.get('map_cached')
            and valid_position(o.get('position'))]
    if len(live) > 1:
        return 'wait', None
    if live:
        obj = live[0]
        if type(obj.get('resource_available')) is not int:
            return 'wait', None
        return ('live' if obj['resource_available'] > 0 else 'unavailable'), obj
    return 'search', None


def patrol_checkpoints(scene):
    if scene.get('house_entrance_state') != 'ready':
        return []
    houses = {}
    for obj in scene.get('house_entrances', ()):
        number, position = obj.get('house_number'), obj.get('position')
        if type(number) is not int or not 1 <= number <= 12 or number in houses \
                or obj.get('source') != 'main-map-house-player-position' or not valid_position(position):
            return []
        houses[number] = list(position)
    return [houses[n] for n in range(1, 13)] if len(houses) == 12 else []


def validate_patrol(data):
    validate_points(data)
    c = data.get('special_patrol', {})
    combined=c.get('category')=='both'
    if (not combined and (c.get('category') not in SPECIAL_IDS or c.get('static_id') != SPECIAL_IDS[c['category']])) \
            or type(c.get('press_count')) is not int or not 1 <= c['press_count'] <= 100 \
            or not c.get('context') or c.get('checkpoint_kind') not in ('house-entrances','house-public-side') \
            or len(data['points']) != 1:
        raise ValueError('房门巡查任务无效')
    positions, numbers = c.get('checkpoints'), c.get('checkpoint_houses')
    if not isinstance(positions, list) or numbers not in ([], list(range(1, 13))) \
            or len(positions) != len(numbers) or any(not valid_position(p) for p in positions):
        raise ValueError('房门巡查点不完整')
    if combined and c.get('categories')!=['fluorite','roaming_oak']:
        raise ValueError('联合巡查种类配置无效')
    if not combined and data['points'][0].get('special_resource') != {k: c[k] for k in ('category', 'static_id', 'press_count')}:
        raise ValueError('巡查与采集目标不一致')
    return data


def make_patrol_data(scene, position, category, name, count, stamp):
    combined=category=='both'
    if category not in (*SPECIAL_IDS,'both') or type(count) is not int or not 1 <= count <= 100:
        raise ValueError('巡查类型或连按次数无效')
    if scene.get('state') != 'ready' or not scene.get('patrol_context') or not valid_position(position):
        raise ValueError('场景与主地图身份尚未确认，请等待同步后启动巡查')
    checkpoints = patrol_checkpoints(scene)
    state, target = patrol_target(scene, category)
    if not checkpoints and state != 'live':
        raise ValueError('1～12号房门入口尚未完整读取，请等待主地图加载后重试')
    seed='fluorite' if combined else category
    identity = {'category': seed, 'static_id': SPECIAL_IDS[seed], 'press_count': count}
    data = {'schema': 'coordinate-points-v1',
            'special_patrol': {**identity, 'category':category,
                               **({'categories':['fluorite','roaming_oak']} if combined else {}),
                               'context': deepcopy(scene['patrol_context']),
                               'checkpoints': checkpoints, 'checkpoint_kind': 'house-entrances',
                               'checkpoint_houses': list(range(1, 13)) if checkpoints else []},
            'points': [{'id': 1, 'name': name, 'position': list(position),
                        'sample_at': stamp, 'saved_at': stamp, 'special_resource': identity}]}
    return validate_patrol(data)
