import json
from pathlib import Path
import tempfile
import unittest

from utils.core.personal_route import RecordingSession, save_route
from utils.core.schema import validate_map


class RecordingTests(unittest.TestCase):
    def test_waits_for_start_and_preserves_duration(self):
        s = RecordingSession()
        s.press('w', 1)
        s.toggle(10)
        s.press('w', 12)
        s.release('w', 14.5)
        self.assertEqual(s.steps, [{'await': 2}, {'w': 2.5}])

    def test_battle_marker_pauses_and_omits_battle_time(self):
        s = RecordingSession()
        s.toggle(0)
        s.action('x', 2)
        self.assertFalse(s.active)
        s.toggle(100)
        s.press('w', 100)
        s.release('w', 101)
        self.assertEqual(s.steps, [{'await': 2}, {'fighting': 1}, {'w': 1}])

    def test_pause_closes_held_key(self):
        s = RecordingSession()
        s.toggle(0)
        s.press('a', 1)
        s.toggle(3)
        s.release('a', 4)
        s.toggle(50)
        s.press('d', 50)
        s.release('d', 51)
        self.assertEqual(s.steps, [{'await': 1}, {'a': 2}, {'d': 1}])

    def test_combined_keys_are_rejected_instead_of_silently_distorted(self):
        s = RecordingSession()
        s.toggle(0)
        s.press('w', 0)
        with self.assertRaises(ValueError):
            s.press('d', 1)

    def test_turn_uses_raw_counts(self):
        s = RecordingSession()
        s.toggle(0)
        s.action('right', 1)
        self.assertEqual(s.steps, [{'await': 1}, {'mouse_delta': 200}])

    def test_finish_flushes_held_key(self):
        s = RecordingSession()
        s.toggle(0)
        s.press('w', 0)
        s.finish(2)
        self.assertEqual(s.steps, [{'w': 2}])

    def test_save_never_overwrites_and_is_engine_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = save_route(Path(tmp), '我的练习', '我', '3', [{'w': 1}])
            p2 = save_route(Path(tmp), '我的练习', '我', '3', [{'w': 2}])
            self.assertNotEqual(p, p2)
            data = json.loads(p.read_text(encoding='utf-8'))
            self.assertEqual(data['start'], [{'normal_run': 1}])
            self.assertTrue(data['manual_start'])
            self.assertEqual(data['name'], '我的练习-1')
            self.assertEqual(validate_map(data), [])
            self.assertEqual(json.loads(p2.read_text(encoding='utf-8'))['map'], [{'w': 2}])

    def test_empty_recording_and_bad_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, planet, steps in [('', '1', [{'w': 1}]), ('路线', '../x', [{'w': 1}]), ('路线', '1', [])]:
                with self.assertRaises(ValueError):
                    save_route(Path(tmp), name, '我', planet, steps)

    def test_delta_passes_schema(self):
        data = dict(name='测试-1', author='我', start=[], map=[{'mouse_delta': -200}])
        self.assertEqual(validate_map(data), [])


if __name__ == '__main__':
    unittest.main()
