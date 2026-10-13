import ctypes
import unittest
from unittest.mock import Mock, patch

from utils.core.input_keys import key_from_vk
from utils.drivers import raw_mouse
from utils.drivers import windows_input


class InputPacketTests(unittest.TestCase):
    def test_keyboard_physical_sides_and_keypad_do_not_collapse(self):
        self.assertEqual(key_from_vk(16, 54), 'shift_r')
        self.assertEqual(key_from_vk(17, 29, True), 'ctrl_r')
        self.assertEqual(key_from_vk(13, 28, True), 'numpad_enter')
        self.assertEqual(key_from_vk(97, 79), 'numpad_1')

    def test_real_raw_mouse_packet_dispatches_movement_buttons_and_signed_wheel(self):
        motion, button, scroll = Mock(), Mock(), Mock()
        reader = raw_mouse.RawMouse(motion, on_button=button, on_scroll=scroll)
        header = raw_mouse.Header(type=0)
        packet = raw_mouse.MouseData(dx=12, dy=-3, buttons=1 | 0x400 | ((65536 - 120) << 16))
        reader.dispatch(bytes(header) + bytes(packet))
        motion.assert_called_once_with(12, -3)
        button.assert_called_once_with('left', True)
        scroll.assert_called_once_with(0, -120)

    def test_raw_keyboard_preserves_scan_extended_and_release(self):
        callback = Mock()
        reader = raw_mouse.RawMouse(Mock(), on_key=callback)
        header = raw_mouse.Header(type=1)
        packet = raw_mouse.KeyboardData(scan=29, flags=3, vk=17)
        reader.dispatch(bytes(header) + bytes(packet))
        callback.assert_called_once_with(17, 29, True, False)

    def test_absolute_packet_is_not_mistaken_for_a_huge_camera_delta(self):
        motion, absolute = Mock(), Mock()
        reader = raw_mouse.RawMouse(motion, on_absolute=absolute)
        reader.dispatch(bytes(raw_mouse.Header(type=0)) + bytes(raw_mouse.MouseData(flags=1, dx=32000, dy=16000)))
        motion.assert_not_called()
        absolute.assert_called_once_with()

    def test_truncated_packet_is_an_error_instead_of_silent_input_loss(self):
        reader = raw_mouse.RawMouse(Mock())
        with self.assertRaises(ValueError):
            reader.dispatch(bytes(raw_mouse.Header(type=0)) + b'\x00')

    def test_sendinput_structure_and_scancode_flags_without_sending_input(self):
        backend = Mock(return_value=1)
        native = windows_input.WindowsInput(send=backend)
        native.key(163, 29, True, False)
        event = backend.call_args.args[1]._obj
        self.assertEqual(ctypes.sizeof(event), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(event.ki.wVk, 0)
        self.assertEqual(event.ki.wScan, 29)
        self.assertEqual(event.ki.dwFlags, 0x8 | 0x2 | 0x1)

    def test_failed_injection_is_reported(self):
        native = windows_input.WindowsInput(send=Mock(return_value=0))
        with self.assertRaises(OSError):
            native.key(87, 17, False, True)

    def test_absolute_mouse_uses_virtual_desktop_coordinates(self):
        native = windows_input.WindowsInput(send=Mock(return_value=1))
        with patch.object(windows_input.win32api, 'GetSystemMetrics', side_effect=[-1920, 0, 3840, 1080]):
            native.position(0, 540)
        event = native.send.call_args.args[1]._obj
        self.assertEqual(event.mi.dwFlags, 0x8000 | 0x4000 | 1)
        self.assertAlmostEqual(event.mi.dx, 32776, delta=1)


if __name__ == '__main__':
    unittest.main()
