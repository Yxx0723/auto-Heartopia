"""Pure cursor over the game's display guidance; never infers ground height."""
import bisect
import hashlib
import json
import math

from utils.core import thresholds as policy
from utils.core.coordinate_route import valid_position


def validate_guidance_state(value, now):
    if not isinstance(value, dict) or value.get('state') != 'ready':
        raise ValueError('游戏地面引导尚未开启或正在更新')
    stamp = value.get('captured_at')
    if type(stamp) not in (int, float) or not math.isfinite(stamp) \
            or not -.05 <= now-stamp <= policy.COORD_GUIDANCE_MAX_AGE:
        raise ValueError('引导状态快照暂未更新，正在重新读取')
    context, target = value.get('context'), value.get('target')
    if not isinstance(context, str) or not 1 <= len(context) <= 256 or not valid_position(target):
        raise ValueError('游戏引导目标或场景身份无效')
    return context, tuple(target)


def validate_guidance(value, now):
    context, target = validate_guidance_state(value, now)
    stamp, path = value['captured_at'], value.get('path')
    if not isinstance(path, (list, tuple)) or not 2 <= len(path) <= policy.COORD_GUIDANCE_MAX_POINTS:
        raise ValueError('游戏引导路径为空或超出读取范围')
    points = []
    for p in path:
        if not valid_position(p):
            raise ValueError('游戏引导路径包含无效坐标')
        if not points or math.dist(points[-1], p) > 1e-6:
            points.append(tuple(p))
    if len(points) < 2:
        raise ValueError('游戏引导路径没有有效长度')
    result = {'context': context, 'target': tuple(target), 'path': tuple(points), 'captured_at': stamp}
    result['revision'] = hashlib.sha256(json.dumps([context, target, points], separators=(',', ':'),
                                                 allow_nan=False).encode()).hexdigest()
    return result


class GuidanceRouteCache:
    """Task-local full route; only health observations have an age deadline."""
    def __init__(self):
        self.route = self.token = self.healthy_at = None
        self.loaded_at = float('-inf')

    def install(self, value, now):
        route = validate_guidance(value, now)
        changed = self.route is None or self.route['revision'] != route['revision']
        self.route, self.token = route, value.get('path_token')
        self.healthy_at, self.loaded_at = value['captured_at'], now
        return changed

    def confirm(self, value, now):
        context, target = validate_guidance_state(value, now)
        if self.route is None or context != self.route['context'] or math.dist(target,self.route['target']) > policy.COORD_REACH_RADIUS:
            return False
        if not self.token or value.get('path_token') != self.token:
            return False
        self.healthy_at = value['captured_at']
        return True

    def require_healthy(self, now):
        if self.route is None or self.healthy_at is None or not -.05 <= now-self.healthy_at <= policy.COORD_GUIDANCE_MAX_AGE:
            raise ValueError('引导状态暂未确认，正在重新读取')
        return self.route


class GuidanceRecovery:
    """Independent consecutive stage outages plus a bounded mixed outage."""
    def __init__(self):
        self.since, self.first = {}, None

    def passed(self, stage):
        self.since.pop(stage, None)

    def failed(self, stage, now):
        if self.first is None:
            self.first = now
        self.since.setdefault(stage, now)
        return now-self.since[stage] >= policy.COORD_GUIDANCE_RETRY_SECONDS or now-self.first >= policy.COORD_GUIDANCE_NO_READY_SECONDS

    def ready(self):
        self.since.clear()
        self.first = None


class GuidanceCursor:
    def __init__(self, snapshot, position):
        self.path, self.target = snapshot['path'], snapshot['target']
        self.context, self.revision = snapshot['context'], snapshot['revision']
        self.lengths = [0.]
        for a, b in zip(self.path, self.path[1:]):
            self.lengths.append(self.lengths[-1]+math.hypot(b[0]-a[0], b[2]-a[2]))
        if self.lengths[-1] < 1e-6:
            raise ValueError('当前引导仅有垂直变化，无法确认步行连接')
        self.arc = self._nearest(position, 0, len(self.path)-1)[0]

    def _nearest(self, position, begin, end):
        candidates = []
        for i in range(begin, end):
            a, b = self.path[i:i+2]
            dx, dz = b[0]-a[0], b[2]-a[2]
            length2 = dx*dx+dz*dz
            t = max(0., min(1., ((position[0]-a[0])*dx+(position[2]-a[2])*dz)/length2)) if length2 else 0.
            p = tuple(u+(v-u)*t for u,v in zip(a,b))
            if abs(p[1]-position[1]) > policy.COORD_GUIDANCE_HEIGHT_HINT_BAND:
                continue
            gap = math.hypot(position[0]-p[0], position[2]-p[2])
            candidates.append((gap, self.lengths[i]+math.sqrt(length2)*t, i))
        if not candidates:
            raise ValueError('人物与引导路径高度层不符，请核对桥面或楼层')
        gap, arc, index = min(candidates)
        if gap > policy.COORD_GUIDANCE_CORRIDOR_RADIUS:
            raise ValueError('人物已偏离引导路线，请重新追踪后再开始')
        return arc, index

    def aim(self, position):
        begin = max(0, bisect.bisect_right(self.lengths, max(0., self.arc-3.))-1)
        end = min(len(self.path)-1, bisect.bisect_left(self.lengths, self.arc+8.)+1)
        arc, index = self._nearest(position, begin, end)
        self.arc = max(self.arc, arc)
        at = min(self.lengths[-1], self.arc+policy.COORD_GUIDANCE_LOOKAHEAD)
        j = min(len(self.path)-2, max(0, bisect.bisect_right(self.lengths, at)-1))
        a, b = self.path[j:j+2]
        span = self.lengths[j+1]-self.lengths[j]
        t = (at-self.lengths[j])/span if span > 1e-9 else 1.
        hint = tuple(u+(v-u)*t for u,v in zip(a,b))
        end_gap = math.hypot(position[0]-self.path[-1][0], position[2]-self.path[-1][2])
        arrived = self.lengths[-1]-self.arc <= policy.COORD_GUIDANCE_END_RADIUS \
            and end_gap <= policy.COORD_GUIDANCE_END_RADIUS \
            and abs(position[1]-self.path[-1][1]) <= policy.COORD_GUIDANCE_HEIGHT_HINT_BAND
        return {'position': (hint[0], position[1], hint[2]), 'height_hint': hint[1],
                'remaining': max(0., self.lengths[-1]-self.arc), 'index': index,
                'arrived': arrived, 'target_distance': math.hypot(position[0]-self.target[0], position[2]-self.target[2])}
