"""Display state shared by the native monitor, independent of Tk and the OS."""
import math

from utils.core.coordinate_route import valid_position
from utils.core import thresholds as policy


def locator_details(status):
    if status.get('locator') == 'mono-local-player':
        lines = [f'{i + 1:02d}  {path["label"]}\n    XYZ → '
                 + (f'0x{path["address"]:X}' if type(path.get('address')) is int else '附着根节点世界坐标')
                 for i, path in enumerate(status.get('paths', []))]
        return '\n'.join(lines), '本地角色对象校验通过'
    lines = [f'{i + 1:02d}  UnityPlayer.dll + 0x{path["rva"]:X}   '
             f'{" → ".join(f"0x{value:X}" for value in path["offsets"])}\n'
             f'    入口 0x{path["root_address"]:X}   XYZ → 0x{path["address"]:X}'
             for i, path in enumerate(status.get('paths', []))]
    return '\n'.join(lines), f'{status.get("valid_paths", 0)} / {status.get("configured_paths", 0)} 条读数一致'


class MonitorView:
    def __init__(self):
        self.position = None
        self.reference = None
        self.trail = []
        self.previous = None
        self.session = None
        self.sequence = None
        self.segment = 0
        self.speed = None
        self.paused = False
        self.last_response = None
        self.status = {'state': 'connecting', 'message': '正在连接心动小镇…'}
        self.notice = ''
        self.heading = None
        self.heading_origin = None

    def invalidate(self, message=None):
        self.position = None
        self.previous = None
        self.speed = None
        self.heading = None
        self.heading_origin = None
        self.segment += 1
        if message:
            self.status = {'state': 'disconnected', 'message': message}

    def accept(self, status, now):
        if self.paused:
            return
        self.last_response = now
        self.status = dict(status)
        if status.get('state') != 'connected':
            self.invalidate()
            return
        if not valid_position(status.get('position')) \
                or status.get('sample_age', 0.) > policy.COORD_MONITOR_STALE_SECONDS:
            self.invalidate('没有可用的实时坐标，请重新连接。')
            return
        if status.get('session') != self.session:
            self.session = status.get('session')
            self.trail.clear()
            self.reference = None
            self.previous = None
            self.sequence = None
            self.heading = None
            self.heading_origin = None
            self.segment += 1
        self.position = tuple(status['position'])
        if status.get('sequence') == self.sequence:
            return
        self.sequence = status.get('sequence')
        point = {'x': self.position[0], 'z': self.position[2], 'at': now, 'segment': self.segment}
        self.notice = ''
        self.speed = 0.
        if self.previous:
            dt = now - self.previous['at']
            dist = math.hypot(point['x'] - self.previous['x'], point['z'] - self.previous['z'])
            if dt <= 0 or dt > policy.COORD_MONITOR_STALE_SECONDS or dist > policy.COORD_MAX_SPEED * dt:
                self.segment += 1
                point['segment'] = self.segment
                self.speed = None
                self.heading = None
                self.heading_origin = None
                self.notice = '位置或采样间隔发生跳变，轨迹已分段。'
            else:
                self.speed = 0. if dist < policy.COORD_IDLE_RADIUS else dist / dt
        if self.heading_origin is not None:
            dx = point['x'] - self.heading_origin['x']
            dz = self.heading_origin['z'] - point['z']
            length = math.hypot(dx, dz)
            if length >= .05:
                self.heading = (dx/length, dz/length)
                self.heading_origin = point
        else:
            self.heading_origin = point
        last = self.trail[-1] if self.trail else None
        if last is None or point['segment'] != last['segment'] \
                or math.hypot(point['x'] - last['x'], point['z'] - last['z']) >= policy.COORD_IDLE_RADIUS:
            self.trail.append(point)
            if len(self.trail) > policy.COORD_MONITOR_TRAIL_POINTS:
                self.trail.pop(0)
        self.previous = point

    def expire(self, now):
        if not self.paused and self.position is not None and self.last_response is not None \
                and now - self.last_response > policy.COORD_MONITOR_STALE_SECONDS:
            self.invalidate('坐标数据已过期，等待恢复连接。')

    def set_paused(self, paused):
        self.paused = paused
        self.previous = None
        self.segment += 1
        if not paused:
            self.invalidate()
            self.status = {'state': 'connecting', 'message': '正在恢复实时读取…'}

    def set_reference(self):
        if self.position is None or self.paused:
            raise ValueError('没有可用的实时位置')
        self.reference = self.position

    def point_snapshot(self, now):
        self.expire(now)
        if self.paused or self.position is None or self.last_response is None \
                or self.status.get('state') != 'connected' \
                or now - self.last_response + self.status.get('sample_age', 0.) > policy.COORD_MONITOR_STALE_SECONDS:
            raise ValueError('没有可保存的实时位置，请恢复显示或等待游戏重新连接。')
        return {'position': list(self.position), 'sample_at': self.status.get('sample_at')}

    def clear_trail(self):
        self.trail.clear()
        self.previous = None
        self.heading_origin = None
        self.segment += 1

    @property
    def delta(self):
        if self.position is None or self.reference is None:
            return None
        return tuple(a - b for a, b in zip(self.position, self.reference))

    @property
    def reference_distance(self):
        delta = self.delta
        return math.hypot(delta[0], delta[2]) if delta is not None else None
