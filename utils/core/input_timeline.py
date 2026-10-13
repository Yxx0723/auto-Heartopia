"""Game-independent input timeline. No window, listener or device dependencies."""
import math
from pathlib import Path

from utils.core.json_io import dumps_map
from utils.core.input_keys import KEY_VK, CONTROL_KEYS

KEYS = set(KEY_VK) - CONTROL_KEYS
BUTTONS = {'left', 'right', 'middle', 'x1', 'x2'}


class Timeline:
    def __init__(self):
        self.events = []
        self.active = False
        self.duration = 0.0
        self.started = 0.0
        self.keys = set()
        self.buttons = set()
        self.key_details = {}

    def at(self, now):
        return round(self.duration + (now - self.started if self.active else 0), 4)

    def resume(self, now):
        if not self.active:
            self.started = now
            self.active = True

    def pause(self, now):
        if not self.active:
            return
        for key in sorted(self.keys):
            self.events.append({'at': self.at(now), 'type': 'key_up', 'key': key,
                                **self.key_details.get(key, {})})
        for button in sorted(self.buttons):
            self.events.append({'at': self.at(now), 'type': 'mouse_up', 'button': button})
        self.keys.clear()
        self.key_details.clear()
        self.buttons.clear()
        self.duration = self.at(now)
        self.active = False

    def key(self, key, down, now, **identity):
        if not self.active or key not in KEYS:
            return
        if down == (key in self.keys):
            return
        (self.keys.add if down else self.keys.discard)(key)
        if down:
            self.key_details[key] = identity
        self.events.append({'at': self.at(now), 'type': 'key_down' if down else 'key_up',
                            'key': key, **self.key_details.get(key, {})})
        if not down:
            self.key_details.pop(key, None)

    def button(self, button, down, now, position=None):
        if not self.active or button not in BUTTONS or down == (button in self.buttons):
            return
        (self.buttons.add if down else self.buttons.discard)(button)
        event = {'at': self.at(now), 'type': 'mouse_down' if down else 'mouse_up', 'button': button}
        if position is not None:
            event.update(x=int(position[0]), y=int(position[1]))
        self.events.append(event)

    def move(self, dx, dy, now, position=None):
        if self.active and (dx or dy):
            event = {'at': self.at(now), 'type': 'mouse_move', 'dx': int(dx), 'dy': int(dy)}
            if position is not None:
                event.update(x=int(position[0]), y=int(position[1]), cursor=True)
            self.events.append(event)

    def position(self, x, y, now):
        if self.active:
            self.events.append({'at': self.at(now), 'type': 'cursor_move', 'x': int(x), 'y': int(y)})

    def scroll(self, dx, dy, now, raw=False):
        if self.active and (dx or dy):
            event = {'at': self.at(now), 'type': 'scroll', 'dx': int(dx), 'dy': int(dy)}
            if raw:
                event['unit'] = 'delta'
            self.events.append(event)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_timeline(data):
    errors = []
    if not isinstance(data, dict) or data.get('engine') != 'desktop_timeline':
        return ['不是小镇键鼠时间轴路线，请重新录制']
    if not isinstance(data.get('target_title'), str) or not data['target_title'].strip():
        errors.append('缺少目标窗口名称')
    size = data.get('client_size')
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size):
        errors.append('缺少有效的录制窗口尺寸')
    duration = data.get('duration')
    if not _number(duration) or duration < 0:
        errors.append('录制时长无效')
    events = data.get('events')
    if not isinstance(events, list) or not events or len(events) > 500000:
        return errors + ['没有有效的录制动作，或动作数量过多']
    keys, buttons = set(), set()
    previous = 0
    for e in events:
        if not isinstance(e, dict) or not _number(e.get('at')) or e['at'] < previous:
            return errors + ['动作时间必须按顺序排列']
        previous = e['at']
        if _number(duration) and previous > duration + 0.001:
            return errors + ['动作超出录制时长']
        kind = e.get('type')
        if kind in ('key_down', 'key_up'):
            key = e.get('key')
            if key not in KEYS:
                return errors + ['路线包含不支持的按键或录制控制键']
            if 'scan' in e and (type(e['scan']) is not int or not 1 <= e['scan'] <= 255 or e['scan'] in (64, 67, 68)):
                return errors + ['键盘扫描码无效']
            if 'extended' in e and type(e['extended']) is not bool:
                return errors + ['键盘扩展标志无效']
            if 'vk' in e and (type(e['vk']) is not int or not 1 <= e['vk'] <= 254 or e['vk'] in (117, 120, 121)):
                return errors + ['键盘虚拟码无效或包含控制键']
            if (kind == 'key_down') == (key in keys):
                return errors + ['按键按下与释放不匹配']
            (keys.add if kind == 'key_down' else keys.discard)(key)
        elif kind in ('mouse_down', 'mouse_up'):
            button = e.get('button')
            if button not in BUTTONS or (kind == 'mouse_down') == (button in buttons):
                return errors + ['鼠标按下与释放不匹配']
            (buttons.add if kind == 'mouse_down' else buttons.discard)(button)
        elif kind in ('mouse_move', 'scroll'):
            if any(type(e.get(k)) is not int or abs(e[k]) > 1000000 for k in ('dx', 'dy')):
                return errors + ['鼠标位移数据无效']
            if kind == 'scroll' and e.get('unit', 'steps') not in ('steps', 'delta'):
                return errors + ['鼠标滚轮单位无效']
            if 'cursor' in e and (type(e['cursor']) is not bool or e['cursor'] and ('x' not in e or 'y' not in e)):
                return errors + ['鼠标指针模式无效']
        elif kind == 'cursor_move':
            pass
        else:
            return errors + ['未知动作类型']
        if kind == 'cursor_move' or 'x' in e or 'y' in e:
            if any(type(e.get(k)) is not int or abs(e[k]) > 1000000 for k in ('x', 'y')):
                return errors + ['鼠标位置数据无效']
    if keys or buttons:
        errors.append('路线结束时存在未释放的按键或鼠标')
    return errors


def save_timeline(folder, name, author, target, timeline, client_size):
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError('请填写80字以内的路线名称')
    if not isinstance(author, str) or not author.strip() or len(author) > 80:
        raise ValueError('请填写80字以内的作者')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    seq = 1
    while True:
        path = folder / f'map_1-1_{seq}.json'
        data = {'name': f'{name.strip()}-{seq}', 'author': author.strip(), 'start': [], 'map': [],
                'engine': 'desktop_timeline', 'target_title': target, 'manual_start': True,
                'input_version': 2, 'client_size': list(client_size),
                'duration': timeline.duration, 'events': timeline.events}
        errors = validate_timeline(data)
        if errors:
            raise ValueError('；'.join(errors))
        try:
            with path.open('x', encoding='utf-8', newline='') as stream:
                stream.write(dumps_map(data))
            return path
        except FileExistsError:
            seq += 1
