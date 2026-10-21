"""Resource interaction standpoints stay outside the resource's solid center."""
import math
from utils.core import thresholds as policy
from utils.core.coordinate_route import distance, valid_position


class SpecialTargetUnavailable(ValueError):
    """A temporary dynamic target may be abandoned by an active patrol."""


def global_special_targets(scene, category, static_id=None):
    if scene.get('state') != 'ready' or scene.get('global_special_state') != 'ready':
        return []
    return [obj for obj in scene.get('global_special_resources', ())
            if obj.get('category') == category and obj.get('resource_global_validated') is True
            and obj.get('resource_global_only') is True and obj.get('map_dynamic') is True
            and not obj.get('map_cached') and valid_position(obj.get('position'))
            and obj.get('resource_static_id') == {'roaming_oak': 42001, 'fluorite': 42002}.get(category)
            and (static_id is None or obj.get('resource_static_id') == static_id)]


def special_search_targets(scene, category, static_id=None):
    if scene.get('state') != 'ready' or scene.get('global_special_state') != 'ready':
        return []
    return [obj for obj in scene.get('special_search_hints', ())
            if obj.get('category') == category and obj.get('resource_search_hint') is True
            and obj.get('resource_global_only') is True and not obj.get('map_cached')
            and valid_position(obj.get('position'))
            and obj.get('resource_static_id') == {'roaming_oak': 42001, 'fluorite': 42002}.get(category)
            and (static_id is None or obj.get('resource_static_id') == static_id)]


def select_special_target(scene, category, name):
    if scene.get('state') != 'ready':
        raise ValueError('场景尚未读取完成，请稍后重试。')
    objects = [obj for obj in scene.get('objects', []) if obj.get('map_dynamic') is True
               and not obj.get('map_cached')]
    matches = [obj for obj in objects if obj.get('category') == category]
    if not matches:
        global_targets = global_special_targets(scene, category)
        if len(global_targets) == 1:
            return global_targets[0]  # Navigation only; availability must be checked after approach.
        if len(global_targets) > 1:
            raise ValueError(f'全图同步到多个{name}，暂不自动选择，请靠近其中一个。')
        hints = special_search_targets(scene, category)
        if len(hints) == 1:
            return hints[0]
        if scene.get('special_resource_status', {}).get(category) == 'unconfirmed-position':
            raise ValueError(f'上次位置已到达核对，但没有识别到{name}实体，已停用旧坐标。'
                             '可能已采集后离开或隐藏，目前尚未确认新位置。')
        names = {'fluorite': '无暇萤石', 'roaming_oak': '溜溜橡木'}
        others = sorted({names[obj['category']] for obj in objects if obj.get('category') in names})
        nearby = f'当前附近识别到{"、".join(others)}，' if others else '当前附近'
        raise ValueError(f'{nearby}尚未加载{name}，全图动态数据也尚未同步到它的位置。'
                         '移动后会继续刷新；未同步不代表全图没有。')
    if len(matches) > 1:
        raise ValueError(f'当前识别到多个{name}，暂不自动选择，请靠近其中一个后刷新场景再试。')
    target = matches[0]
    if not valid_position(target.get('position')) or type(target.get('resource_available')) is not int:
        raise ValueError(f'{name}已加载，但位置或采集状态尚未读完整，请刷新场景后重试。')
    if target['resource_available'] <= 0:
        raise ValueError(f'{name}已加载，但当前采集标志不可用，可能处于冷却或已采集。请先在游戏中核对。')
    return target


def in_interaction_range(position, resource):
    return distance(position, resource) <= policy.COORD_SPECIAL_INTERACTION_RADIUS \
        and abs(position[1]-resource[1]) <= policy.COORD_MAX_HEIGHT_ERROR


def interaction_standpoint(position, resource, obstacles, avoid=()):
    if in_interaction_range(position, resource):
        return list(position)
    angle = math.atan2(position[2]-resource[2], position[0]-resource[0])
    candidates = []
    for radius in (.9, 1.05, 1.2):
        for turn in range(24):
            theta = angle+turn*math.tau/24
            point = [resource[0]+radius*math.cos(theta), resource[1], resource[2]+radius*math.sin(theta)]
            obstacles.set_goal(point)
            if obstacles.clear((point[0], point[2]), (point[0], point[2])):
                candidates.append(point)
    if not candidates:
        raise ValueError('资源附近暂未找到可站立的交互位置')
    # A spot that already proved unreachable must not be chosen again, or the
    # retry would walk the same blocked line forever. The 2026-10-20 oak trace
    # slid against the trunk for 9 s because the blocking body was not in the
    # obstacle map at all, so reachability is judged by walking, not by the map.
    fresh = [point for point in candidates
             if all(distance(point, seen) > policy.COORD_SPECIAL_STANDPOINT_REUSE for seen in avoid)]
    pool = fresh or candidates
    return min(pool, key=lambda point: distance(position, point))


class ApproachProgress:
    """Detect a final-approach stall that the coarse stuck rule cannot see.

    ``COORD_STUCK_DISTANCE`` (.15) never triggers while the last .1-.3 units of
    a resource approach are being blocked; the character then slides against the
    model for seconds. This tracks progress against the resource centre instead.
    """

    def __init__(self):
        self.best, self.at = math.inf, None

    def reset(self):
        self.best, self.at = math.inf, None

    def stalled(self, position, resource, now):
        gap = distance(position, resource)
        if gap > policy.COORD_SPECIAL_APPROACH_WATCH_RADIUS:
            self.best, self.at = gap, now
            return False
        if gap < self.best - policy.COORD_SPECIAL_APPROACH_STALL_DISTANCE:
            self.best, self.at = gap, now
            return False
        if self.at is None:
            self.best, self.at = gap, now
            return False
        return now - self.at >= policy.COORD_SPECIAL_APPROACH_STALL_SECONDS
