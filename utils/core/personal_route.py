"""Personal recording state and canonical route persistence; no device access."""
from pathlib import Path
import math

from utils.core.json_io import dumps_map
from utils.core.schema import validate_map


class RecordingSession:
    def __init__(self):
        self.active = False
        self.steps = []
        self.held = None
        self.last_end = 0

    def _gap(self, now):
        gap = round(now - self.last_end, 3)
        if gap > 0.05:
            self.steps.append({'await': gap})

    def toggle(self, now):
        if self.active:
            self.finish(now)
        else:
            self.active = True
            self.last_end = now
        return self.active

    def press(self, key, now):
        if not self.active:
            return
        if self.held:
            if self.held[0] == key:
                return  # OS autorepeat
            raise ValueError('请逐个按键录制，不能同时按住两个操作键；本次录制未保存。')
        if key not in {'w', 'a', 's', 'd', 'v'}:
            raise ValueError('不支持的持续按键')
        self._gap(now)
        self.held = (key, now)

    def release(self, key, now):
        if self.held and self.held[0] == key:
            duration = max(0.001, round(now - self.held[1], 3))
            self.steps.append({'await' if key == 'v' else key: duration})
            self.held = None
            self.last_end = now

    def action(self, key, now):
        if not self.active:
            return
        if self.held:
            raise ValueError('请松开移动键后再交互、转向或攻击；本次录制未保存。')
        actions = {'x': ('fighting', 1), 'click': ('fighting', 2),
                   'e': ('e', 2), 'f': ('f', 15), 'r': ('r', 1),
                   'space': ('space', 1), 'esc': ('esc', 1),
                   'left': ('mouse_delta', -200), 'right': ('mouse_delta', 200)}
        if key not in actions:
            raise ValueError('不支持的操作')
        self._gap(now)
        op, value = actions[key]
        self.steps.append({op: value})
        self.last_end = now
        if key == 'x':
            self.active = False  # Battle time must not be replayed as an idle wait.

    def finish(self, now):
        if self.held:
            self.release(self.held[0], now)
        self.active = False


def validate_recording_metadata(name, author, planet):
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError('路线名称不能为空，且不能超过80字')
    if not isinstance(author, str) or not author.strip() or len(author) > 80:
        raise ValueError('作者不能为空，且不能超过80字')
    if str(planet) not in {'1', '2', '3', '4', '5', '6'}:
        raise ValueError('请选择有效星球（1至6）')


def save_route(folder, name, author, planet, steps):
    validate_recording_metadata(name, author, planet)
    if not steps:
        raise ValueError('还没有录到动作，未创建空路线')
    for step in steps:
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError('路线步骤格式错误')
        value = next(iter(step.values()))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('路线步骤值必须是有限数字')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    seq = 1
    while True:
        path = folder / f'map_{planet}-1_{seq}.json'
        data = {'name': f'{name.strip()}-{seq}', 'author': author.strip(),
                'manual_start': True, 'personal_route': True,
                'start': [{'normal_run': 1}], 'map': steps}
        issues = validate_map(data)
        if issues:
            raise ValueError('; '.join(str(i) for i in issues))
        try:
            with path.open('x', encoding='utf-8', newline='') as stream:
                stream.write(dumps_map(data))
            return path
        except FileExistsError:
            seq += 1
