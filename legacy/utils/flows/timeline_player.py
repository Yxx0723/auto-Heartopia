"""Timed playback with injectable devices, controls and clock."""
import json
from pathlib import Path
import re

from utils.core.input_timeline import validate_timeline
from utils.drivers.timeline_hotkeys import PlaybackControls
from utils.drivers.town_window import TownWindow, TimelineInput
from utils.drivers.windows_input import PreciseClock
from utils.ui.recorder_feedback import Feedback


def play_events(data, device, window, controls, clock=PreciseClock):
    elapsed = 0.0
    previous = clock.monotonic()
    index = 0
    desired_keys, desired_buttons = set(), set()
    was_running = False
    duration = data.get('duration', data['events'][-1]['at'])
    try:
        while index < len(data['events']) or elapsed < duration:
            now = clock.monotonic()
            running, stopped = controls.snapshot()
            if stopped:
                return False
            if running and (not window.foreground() or window.size() != data['client_size']):
                controls.pause()
                running = False
            if not running:
                if was_running:
                    device.release_all()
                was_running = False
                previous = now
                clock.sleep(0.01)
                continue
            if was_running:
                elapsed += now - previous
            else:
                device.restore(set(desired_keys), set(desired_buttons))
            previous, was_running = now, True
            if index == len(data['events']):
                if elapsed < duration:
                    clock.sleep(min(0.01, duration - elapsed))
                continue
            event = data['events'][index]
            if elapsed + 0.0001 >= event['at']:
                # Recheck focus immediately before each injection.
                if not window.foreground():
                    controls.pause()
                    continue
                device.execute(event)
                if event['type'] in {'key_down', 'key_up'}:
                    (desired_keys.add if event['type'] == 'key_down' else desired_keys.discard)(event['key'])
                elif event['type'] in {'mouse_down', 'mouse_up'}:
                    (desired_buttons.add if event['type'] == 'mouse_down' else desired_buttons.discard)(event['button'])
                index += 1
            else:
                clock.sleep(min(0.01, event['at'] - elapsed))
        return True
    finally:
        device.release_all()


def play_main(base, map_id):
    if map_id and re.fullmatch(r'[1-6]-\d+_\d+', map_id):
        try:
            data = json.loads((Path(base) / 'map/default' / ('map_' + map_id + '.json')).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = None  # Existing entry reports file/format errors visibly.
        if isinstance(data, dict) and data.get('engine') == 'coordinate_route':
            from utils.flows.coordinate_player import play_main as play_coordinates
            return play_coordinates(base, data)
    return _play_timeline_main(base, map_id)


def _play_timeline_main(base, map_id):
    base = Path(base)
    feedback = Feedback(base)
    controls = None
    try:
        if not map_id or not re.fullmatch(r'[1-6]-\d+_\d+', map_id):
            raise ValueError('请在控制面板选择一条自己的路线再回放')
        data = json.loads((base / 'map/default' / ('map_' + map_id + '.json')).read_text(encoding='utf-8'))
        errors = validate_timeline(data)
        if errors:
            raise ValueError('；'.join(errors))
        window = TownWindow(data['target_title'])
        controls = PlaybackControls(window, data['client_size'], feedback,
                                    lambda: (base / 'logs/stop-request').exists())
        controls.start()
        completed = play_events(data, TimelineInput(window), window, controls)
        feedback.update('finished' if completed else 'stopped', '路线回放完成' if completed else '回放已停止，按键已释放')
    except Exception as exc:
        feedback.update('error', f'回放失败：{exc}')
        raise
    finally:
        if controls:
            controls.close()
        feedback.close()
