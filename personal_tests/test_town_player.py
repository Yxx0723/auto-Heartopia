import unittest
from unittest.mock import Mock

from utils.flows.timeline_player import play_events
from utils.drivers.town_window import TimelineInput


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TownPlayerTests(unittest.TestCase):
    def test_camera_drag_keeps_relative_motion_even_with_visible_cursor(self):
        native, window = Mock(), Mock()
        device = TimelineInput(window, native)
        device.execute({'type': 'mouse_down', 'button': 'right'})
        device.execute({'type': 'mouse_move', 'dx': 12, 'dy': -4, 'x': 400, 'y': 300, 'cursor': True})
        native.position.assert_not_called()
        self.assertEqual(native.mouse.call_args.args[1:], (12, -4))

    def test_resume_restores_modifiers_before_other_keys(self):
        native = Mock()
        TimelineInput(native=native).restore({'w', 'shift_l', 'ctrl_l'}, set())
        self.assertEqual([c.args[0] for c in native.key.call_args_list], [162, 160, 87])

    def test_physical_key_identity_survives_release_and_resume(self):
        native = Mock()
        device = TimelineInput(native=native)
        device.execute({'type': 'key_down', 'key': 'ctrl_r', 'vk': 163, 'scan': 29, 'extended': True})
        device.release_all()
        device.restore({'ctrl_r'}, set())
        self.assertEqual([c.args for c in native.key.call_args_list],
                         [(163, 29, True, True), (163, 29, True, False), (163, 29, True, True)])

    def test_click_is_anchored_to_current_client_origin(self):
        native, window = Mock(), Mock()
        window.get_rect.return_value = (200, 100, 1000, 700)
        device = TimelineInput(window, native)
        device.execute({'type': 'mouse_down', 'button': 'left', 'x': 400, 'y': 300})
        native.position.assert_called_once_with(600, 400)
        self.assertTrue(device.buttons)
        device.release_all()
        self.assertFalse(device.buttons)

    def test_fractional_raw_wheel_is_not_multiplied_by_120(self):
        native = Mock()
        TimelineInput(native=native).execute({'type': 'scroll', 'dx': 0, 'dy': -30, 'unit': 'delta'})
        self.assertEqual(native.mouse.call_args.kwargs['data'], -30)

    def setup_play(self):
        clock, device, window, controls = Clock(), Mock(), Mock(), Mock()
        window.foreground.return_value = True
        window.size.return_value = [800, 600]
        controls.snapshot.return_value = (True, False)
        data = {'client_size': [800, 600], 'events': [
            {'at': 0, 'type': 'key_down', 'key': 'w'},
            {'at': 0.5, 'type': 'key_up', 'key': 'w'}]}
        return data, clock, device, window, controls

    def test_relative_timing_and_final_release(self):
        data, clock, device, window, controls = self.setup_play()
        seen = []
        device.execute.side_effect = lambda e: seen.append((e, clock.now))
        self.assertTrue(play_events(data, device, window, controls, clock))
        self.assertAlmostEqual(seen[1][1] - seen[0][1], 0.5, places=3)
        device.release_all.assert_called_once()

    def test_waiting_for_f6_never_injects(self):
        data, clock, device, window, controls = self.setup_play()
        controls.snapshot.side_effect = [(False, False), (False, False), (False, True)]
        self.assertFalse(play_events(data, device, window, controls, clock))
        device.execute.assert_not_called()
        device.restore.assert_not_called()

    def test_trailing_wait_is_preserved(self):
        data, clock, device, window, controls = self.setup_play()
        data['duration'] = 1.5
        self.assertTrue(play_events(data, device, window, controls, clock))
        self.assertAlmostEqual(clock.now, 1.5, places=3)

    def test_pause_time_is_excluded_and_held_keys_are_restored(self):
        data, clock, device, window, controls = self.setup_play()

        def status():
            return not (0.1 <= clock.now < 1.1), False

        controls.snapshot.side_effect = status
        times = []
        device.execute.side_effect = lambda e: times.append(clock.now)
        self.assertTrue(play_events(data, device, window, controls, clock))
        self.assertAlmostEqual(times[-1], 1.51, delta=0.03)
        self.assertIn(({'w'}, set()), [c.args for c in device.restore.call_args_list])

    def test_exception_after_press_releases_keys(self):
        data, clock, device, window, controls = self.setup_play()
        device.execute.side_effect = RuntimeError('device error')
        with self.assertRaises(RuntimeError):
            play_events(data, device, window, controls, clock)
        device.release_all.assert_called_once()

    def test_focus_loss_pauses_and_releases_held_keys(self):
        data, clock, device, window, controls = self.setup_play()
        window.foreground.side_effect = [True, True, False]
        controls.snapshot.side_effect = [(True, False), (True, False), (False, True)]
        self.assertFalse(play_events(data, device, window, controls, clock))
        controls.pause.assert_called_once()
        self.assertEqual(device.execute.call_count, 1)
        self.assertGreaterEqual(device.release_all.call_count, 2)


if __name__ == '__main__':
    unittest.main()
