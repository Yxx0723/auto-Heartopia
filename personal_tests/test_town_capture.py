import sys
import pytest
if sys.platform != 'win32':
    pytest.skip('Windows native input/window contract', allow_module_level=True)

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from pynput import mouse

from utils.core.input_timeline import Timeline
from utils.drivers import timeline_capture


def key(name):
    return SimpleNamespace(name=name)


class TownCaptureTests(unittest.TestCase):
    def test_raw_physical_keys_held_across_pause_resume_are_recorded_again(self):
        c, _, _, _ = self.capture()
        c.raw_key(87, 17, False, True)
        c.press(key('f6'))
        c.release(key('f6'))
        c.press(key('f6'))
        c.release(key('f6'))
        c.press(key('f6'))
        c.raw_key(87, 17, False, False)
        self.assertEqual([e['type'] for e in c.timeline.events],
                         ['key_down', 'key_up', 'key_down', 'key_up'])
        self.assertTrue(all(e['scan'] == 17 for e in c.timeline.events))

    def test_visible_cursor_uses_client_positions_hidden_cursor_uses_raw_motion(self):
        c, window, _, _ = self.capture()
        c.press(key('f6'))
        window.pointer.return_value = (400, 300, True)
        c.motion(100, 50)
        c.raw_button('left', True)
        c.raw_button('left', False)
        window.pointer.return_value = (960, 540, False)
        c.motion(12, -4)
        self.assertEqual([e['type'] for e in c.timeline.events],
                         ['mouse_move', 'mouse_down', 'mouse_up', 'mouse_move'])
        self.assertEqual(c.timeline.events[0]['dx'], 100)
        self.assertTrue(c.timeline.events[0]['cursor'])
        self.assertEqual(c.timeline.events[1]['x'], 400)
        self.assertEqual(c.timeline.events[-1]['dx'], 12)

    def capture(self):
        window = Mock(title='心动小镇', hwnd=0)
        window.bind_foreground.return_value = True
        window.foreground.return_value = True
        window.size.return_value = [1920, 1080]
        window.pointer.return_value = (960, 540, False)
        feedback, started = Mock(), Mock()
        capture = timeline_capture.Capture(Timeline(), window, feedback, remember_start=started, clock=lambda: 10)
        return capture, window, feedback, started

    def test_f6_binds_game_only_after_listener_is_ready(self):
        capture, window, feedback, started = self.capture()
        raw = Mock(error=None)

        def ready(*args, **kwargs):
            window.bind_foreground.assert_not_called()
            capture.press(key('f6'))
            capture.release(key('f6'))
            capture.press(key('w'))
            capture.press(key('shift'))
            capture.press(key('f9'))

        feedback.update.side_effect = lambda state, *a, **kw: ready() if state == 'ready' else None
        with patch.object(timeline_capture, 'RawMouse', return_value=raw):
            self.assertTrue(capture.run())
        raw.start.assert_called_once()
        started.assert_called_once()
        self.assertEqual([e['type'] for e in capture.timeline.events],
                         ['key_down', 'key_down', 'key_up', 'key_up'])
        raw.close.assert_called_once()

    def test_wrong_window_reports_reason_and_can_retry(self):
        c, window, feedback, started = self.capture()
        window.bind_foreground.return_value = False
        c.press(key('f6'))
        self.assertFalse(c.timeline.active)
        c.flush_feedback()
        self.assertIn('请切回心动小镇', feedback.update.call_args.args[1])
        started.assert_not_called()
        c.release(key('f6'))
        window.bind_foreground.return_value = True
        c.press(key('f6'))
        self.assertTrue(c.timeline.active)

    def test_key_repeat_does_not_toggle_pause_and_control_keys_are_not_saved(self):
        c, _, _, _ = self.capture()
        c.press(key('f6'))
        c.press(key('f6'))
        self.assertTrue(c.timeline.active)
        c.release(key('f6'))
        c.press(key('f6'))
        self.assertFalse(c.timeline.active)
        self.assertEqual(c.timeline.events, [])

    def test_other_windows_input_is_not_recorded(self):
        c, window, _, _ = self.capture()
        c.press(key('f6'))
        window.foreground.return_value = False
        c.press(key('w'))
        c.motion(10, 3)
        self.assertEqual(c.timeline.events, [])

    def test_cancel_finishes_without_save(self):
        c, _, _, _ = self.capture()
        c.press(key('f10'))
        self.assertTrue(c.cancelled)
        self.assertTrue(c.done.is_set())

    def test_real_mouse_listener_accepts_clicks_while_waiting_for_f6(self):
        c, _, _, _ = self.capture()
        # Construct the real adapter, without starting hooks or sending input.
        listener = mouse.Listener(on_click=c.click, on_scroll=c.scroll)
        listener.on_click(100, 200, mouse.Button.left, True, False)
        listener.on_click(100, 200, mouse.Button.left, False, False)
        self.assertEqual(c.timeline.events, [])

    def test_real_mouse_listener_records_all_three_buttons(self):
        c, _, _, _ = self.capture()
        c.press(key('f6'))
        listener = mouse.Listener(on_click=c.click, on_scroll=c.scroll)
        for button in (mouse.Button.left, mouse.Button.right, mouse.Button.middle):
            listener.on_click(100, 200, button, True, False)
            listener.on_click(100, 200, button, False, False)
        self.assertTrue(c.timeline.active)
        self.assertEqual([(e['type'], e['button']) for e in c.timeline.events],
                         [(kind, button) for button in ('left', 'right', 'middle')
                          for kind in ('mouse_down', 'mouse_up')])

    def test_real_mouse_listener_records_wheel_with_injected_argument(self):
        c, _, _, _ = self.capture()
        c.press(key('f6'))
        listener = mouse.Listener(on_click=c.click, on_scroll=c.scroll)
        listener.on_scroll(100, 200, 0, -1, False)
        listener.on_scroll(100, 200, 1, 0, False)
        self.assertTrue(c.timeline.active)
        self.assertEqual([(e['type'], e['dx'], e['dy']) for e in c.timeline.events],
                         [('scroll', 0, -1), ('scroll', 1, 0)])

    def test_mouse_callbacks_still_accept_legacy_four_arguments(self):
        c, _, _, _ = self.capture()
        c.press(key('f6'))
        c.click(100, 200, mouse.Button.left, True)
        c.click(100, 200, mouse.Button.left, False)
        c.scroll(100, 200, 0, 1)
        self.assertEqual(len(c.timeline.events), 3)


if __name__ == '__main__':
    unittest.main()
