"""Coordinate route contracts and geometry; no game or device dependencies."""
import math
from pathlib import Path

from utils.core.json_io import dumps_map
from utils.core.personal_route import validate_recording_metadata
from utils.core import thresholds as policy


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def valid_position(value):
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(
        finite_number(v) and abs(v) < 100000 for v in value)


def distance(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


def validate_coordinate_route(data):
    if not isinstance(data, dict) or data.get('engine') != 'coordinate_route' \
            or data.get('coordinate_version') != 1:
        return ['坐标路线版本不支持']
    if any(not isinstance(data.get(k), str) or not data[k].strip() or len(data[k]) > 80
           for k in ('name', 'author', 'target_title')):
        return ['路线名称、作者或游戏名称无效']
    if data.get('manual_start') is not True:
        return ['坐标路线需要在游戏内手动开始']
    size = data.get('client_size')
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or not 64 <= v <= 16384 for v in size):
        return ['游戏窗口尺寸无效']
    duration, samples = data.get('duration'), data.get('samples')
    if not finite_number(duration) or not 0 <= duration <= policy.COORD_MAX_DURATION:
        return ['坐标路线时长无效或超过一小时']
    if not isinstance(samples, list) or not 2 <= len(samples) <= policy.COORD_MAX_SAMPLES:
        return ['坐标路线至少需要两个位置，且不能超过一小时']
    previous = None
    for sample in samples:
        if not isinstance(sample, dict) or not finite_number(sample.get('at')) \
                or not 0 <= sample['at'] <= duration or not valid_position(sample.get('position')):
            return ['坐标采样格式无效']
        if previous is not None:
            dt = sample['at'] - previous['at']
            if dt <= 0 or math.dist(sample['position'], previous['position']) > policy.COORD_MAX_SPEED * dt + .05:
                return ['坐标采样时间倒退、重复或存在跳变']
        previous = sample
    return []


class PositionTrack:
    def __init__(self):
        self.samples = []
        self.active = False
        self.duration = 0.0
        self.started = 0.0
        self.resuming = False

    @property
    def events(self):
        return self.samples

    def at(self, now):
        return round(self.duration + (now - self.started if self.active else 0), 4)

    def resume(self, now):
        if not self.active:
            self.started, self.active, self.resuming = now, True, True

    def pause(self, now):
        if self.active:
            self.duration = self.at(now)
            self.active = False

    def sample(self, position, now):
        if not self.active:
            return
        if not valid_position(position):
            raise ValueError('读取到无效角色坐标')
        if len(self.samples) >= policy.COORD_MAX_SAMPLES or self.at(now) > policy.COORD_MAX_DURATION:
            raise ValueError('单条坐标路线最长一小时，请保存后新建路线')
        if self.samples:
            previous = self.samples[-1]
            if self.resuming and (distance(position, previous['position']) > policy.COORD_REACH_RADIUS
                                  or abs(position[1] - previous['position'][1]) > policy.COORD_REACH_RADIUS):
                raise ValueError('暂停期间发生未记录的位移，请回到暂停点后再录制')
            dt = self.at(now) - previous['at']
            if dt <= 0:
                return
            if math.dist(position, previous['position']) > policy.COORD_MAX_SPEED * dt + .05:
                raise ValueError('角色坐标发生跳变，已停止录制')
        self.resuming = False
        self.samples.append({'at': self.at(now), 'position': [round(v, 6) for v in position]})

    def ignore_input(self, *args, **kwargs):
        pass

    key = button = move = position = scroll = ignore_input


def save_coordinates(folder, name, author, target, track, size):
    validate_recording_metadata(name, author, '1')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    seq = 1
    while True:
        file = folder / f'map_1-1_{seq}.json'
        suffix = f'-{seq}'
        data = {'name': name.strip()[:80 - len(suffix)] + suffix, 'author': author.strip(), 'start': [], 'map': [],
                'engine': 'coordinate_route', 'coordinate_version': 1, 'target_title': target,
                'manual_start': True, 'client_size': list(size), 'duration': track.duration,
                'samples': track.samples}
        errors = validate_coordinate_route(data)
        if errors:
            raise ValueError('；'.join(errors))
        try:
            with file.open('x', encoding='utf-8', newline='') as stream:
                stream.write(dumps_map(data))
            return file
        except FileExistsError:
            seq += 1


def build_waypoints(samples):
    points = [{'position': list(samples[0]['position']), 'wait': 0.0}]
    idle = 0.0

    def add(position, wait=0.0):
        if distance(position, points[-1]['position']) <= policy.COORD_IDLE_RADIUS \
                and abs(position[1] - points[-1]['position'][1]) <= policy.COORD_IDLE_RADIUS:
            points[-1]['wait'] += wait
        else:
            points.append({'position': list(position), 'wait': wait})

    for before, current in zip(samples, samples[1:]):
        if distance(current['position'], before['position']) <= policy.COORD_IDLE_RADIUS \
                and abs(current['position'][1] - before['position'][1]) <= policy.COORD_IDLE_RADIUS:
            idle += current['at'] - before['at']
            continue
        if idle >= policy.COORD_DWELL_SECONDS:
            add(before['position'], idle)
        idle = 0.0
        if distance(current['position'], points[-1]['position']) >= policy.COORD_WAYPOINT_SPACING:
            add(current['position'])
    add(samples[-1]['position'], idle if idle >= policy.COORD_DWELL_SECONDS else 0)
    return points


def make_basis(forward, right):
    f, r = (forward[0], forward[2]), (right[0], right[2])
    fl, rl = math.hypot(*f), math.hypot(*r)
    if not all(math.isfinite(v) for v in (*f, *r)) or min(fl, rl) < policy.COORD_MIN_CALIBRATION \
            or max(fl, rl) > policy.COORD_MAX_CALIBRATION_DISTANCE:
        raise ValueError('方向校准没有读到有效位移，请站在空旷平地')
    f, r = [v / fl for v in f], [v / rl for v in r]
    if abs(sum(a * b for a, b in zip(f, r))) > policy.COORD_CALIBRATION_COSINE:
        raise ValueError('前进和横移方向无法区分，请确认角色能自由走动')
    return {'forward': f, 'right': r}


def key_vector(keys, basis):
    f = int('w' in keys) - int('s' in keys)
    r = int('d' in keys) - int('a' in keys)
    return [f * a + r * b for a, b in zip(basis['forward'], basis['right'])]


def direction_keys(position, target, basis, reach_radius=None):
    dx, dz = target[0] - position[0], target[2] - position[2]
    if math.hypot(dx, dz) <= (policy.COORD_REACH_RADIUS if reach_radius is None else reach_radius):
        return set()
    options = [set(k) for k in ('w', 'd', 's', 'a', 'wd', 'ds', 'sa', 'aw')]
    def score(keys):
        vector = key_vector(keys, basis)
        return (vector[0] * dx + vector[1] * dz) / math.hypot(*vector)
    return max(options, key=score)


def rotate_basis(basis, keys, displacement):
    measured = [displacement[0], displacement[2]]
    if not keys or math.hypot(*measured) < policy.COORD_MIN_CALIBRATION:
        return basis
    expected = key_vector(keys, basis)
    angle = math.atan2(measured[1], measured[0]) - math.atan2(expected[1], expected[0])
    angle = math.atan2(math.sin(angle), math.cos(angle))
    if abs(math.degrees(angle)) > policy.COORD_MAX_DIRECTION_CHANGE_DEGREES:
        raise ValueError('实际移动方向与校准不符，请停止后重新校准')
    angle *= .5
    cosine, sine = math.cos(angle), math.sin(angle)
    return {name: [v[0] * cosine - v[1] * sine, v[0] * sine + v[1] * cosine]
            for name, v in basis.items()}
