from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from utils.core.personal_route import RecordingSession
from utils.drivers import personal_input


class DriverTests(unittest.TestCase):
    def drive(self, events, foreground=42):
        session = RecordingSession()
        started = Mock()

        class Listener:
            def __init__(self, **callbacks):
                self.callbacks = callbacks
                self.alive = False

            def start(self):
                self.alive = True
                for kind, name in events:
                    callback = self.callbacks.get('on_' + kind)
                    if callback and callback(SimpleNamespace(name=name)) is False:
                        break
                self.alive = False

            def stop(self):
                self.alive = False

            def join(self, timeout=None):
                pass

            def is_alive(self):
                return self.alive

        class MouseListener(Listener):
            def start(self):
                self.alive = True

        with patch.object(personal_input.keyboard, 'Listener', Listener), \
                patch.object(personal_input.mouse, 'Listener', MouseListener), \
                patch.object(personal_input.win32gui, 'GetForegroundWindow', return_value=foreground), \
                patch.object(personal_input, 'move_relative') as turn:
            personal_input.record_session(session, SimpleNamespace(hwnd=42), started)
        return session, started, turn

    def test_wait_then_start_move_and_save_closes_listeners(self):
        session, started, _ = self.drive([
            ('press', 'w'), ('release', 'w'),
            ('press', 'f6'), ('release', 'f6'),
            ('press', 'w'), ('release', 'w'), ('press', 'f9')])
        self.assertTrue(any('w' in step for step in session.steps))
        self.assertFalse(session.active)
        started.assert_called_once()

    def test_other_app_cannot_start_recording(self):
        session, started, _ = self.drive([('press', 'f6'), ('release', 'f6'), ('press', 'f9')], foreground=99)
        self.assertEqual(session.steps, [])
        started.assert_not_called()

    def test_unsupported_sprint_fails_without_silent_recording(self):
        with self.assertRaisesRegex(ValueError, '组合键'):
            self.drive([('press', 'f6'), ('release', 'f6'), ('press', 'shift')])

    def test_arrow_sends_same_raw_delta_as_recorded(self):
        session, _, turn = self.drive([
            ('press', 'f6'), ('release', 'f6'), ('press', 'right'), ('release', 'right'), ('press', 'f9')])
        turn.assert_called_once_with(200)
        self.assertIn({'mouse_delta': 200}, session.steps)


if __name__ == '__main__':
    unittest.main()
