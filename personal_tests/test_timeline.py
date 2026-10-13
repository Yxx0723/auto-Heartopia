import json
from pathlib import Path
import tempfile
import unittest

from utils.core.input_timeline import Timeline, save_timeline, validate_timeline


class TimelineTests(unittest.TestCase):
    def test_common_game_keys_are_not_silently_dropped(self):
        t = Timeline()
        t.resume(0)
        for name in ('tab', 'enter', 'backspace', 'delete', 'home', 'page_up', 'f11', 'numpad_1', 'semicolon'):
            t.key(name, True, 1)
            t.key(name, False, 2)
        self.assertEqual(len(t.events), 18)

    def test_short_mouse_packets_keep_their_direction_and_timing(self):
        t = Timeline()
        t.resume(0)
        t.move(10, 0, 1)
        t.move(-10, 0, 1.004)
        self.assertEqual([(e['at'], e['dx']) for e in t.events], [(1, 10), (1.004, -10)])

    def test_click_position_and_physical_key_survive_pause_and_save(self):
        t = Timeline()
        t.resume(0)
        t.key('ctrl_r', True, 1, scan=29, extended=True, vk=163)
        t.button('left', True, 1.2, position=(400, 200))
        t.pause(2)
        self.assertEqual(t.events[0]['scan'], 29)
        self.assertTrue(t.events[2]['extended'])
        self.assertEqual((t.events[1]['x'], t.events[1]['y']), (400, 200))

    def test_hotkey_waiting_does_not_record_until_started(self):
        t = Timeline()
        t.key('w', True, 1)
        self.assertEqual(t.events, [])
        t.resume(10)
        t.key('w', True, 11)
        t.key('shift', True, 11.5)
        t.key('w', False, 12)
        t.pause(13)
        self.assertEqual([(e['type'], e['key']) for e in t.events],
                         [('key_down', 'w'), ('key_down', 'shift'), ('key_up', 'w'), ('key_up', 'shift')])
        self.assertEqual(t.duration, 3)

    def test_pause_excludes_time_and_releases_all_buttons(self):
        t = Timeline()
        t.resume(0)
        t.button('right', True, 1)
        t.pause(2)
        t.resume(100)
        t.key('a', True, 101)
        t.pause(102)
        self.assertEqual(t.duration, 4)
        self.assertEqual(t.events[1], {'at': 2, 'type': 'mouse_up', 'button': 'right'})
        self.assertEqual(t.events[2]['at'], 3)

    def test_raw_camera_motion_keeps_device_counts(self):
        t = Timeline()
        t.resume(0)
        t.move(12, -6, 1)
        self.assertEqual(t.events, [{'at': 1, 'type': 'mouse_move', 'dx': 12, 'dy': -6}])

    def test_hotkeys_are_never_replayed(self):
        t = Timeline()
        t.resume(0)
        t.key('f6', True, 1)
        t.key('f9', True, 1)
        self.assertEqual(t.events, [])

    def test_save_is_valid_and_cannot_overwrite(self):
        t = Timeline()
        t.resume(0)
        t.key('w', True, 0)
        t.pause(1)
        with tempfile.TemporaryDirectory() as d:
            args = (Path(d), '小镇练习', '我', '心动小镇', t, [1920, 1080])
            a, b = save_timeline(*args), save_timeline(*args)
            self.assertNotEqual(a, b)
            data = json.loads(a.read_text(encoding='utf-8'))
            self.assertEqual(validate_timeline(data), [])
            self.assertEqual(data['target_title'], '心动小镇')
            self.assertEqual(data['engine'], 'desktop_timeline')

    def test_bad_event_and_unreleased_key_are_rejected(self):
        base = {'engine': 'desktop_timeline', 'target_title': '心动小镇', 'duration': 1,
                'client_size': [1920, 1080], 'events': [{'at': 0, 'type': 'key_down', 'key': 'w'}]}
        self.assertTrue(validate_timeline(base))
        base['events'] = [{'at': 0, 'type': 'key_down', 'key': 'f6'}]
        self.assertTrue(validate_timeline(base))


if __name__ == '__main__':
    unittest.main()
