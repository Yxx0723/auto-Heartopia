import queue
import unittest
from unittest.mock import patch

from utils.drivers import record_overlay


class OverlayTests(unittest.TestCase):
    def test_native_font_contract_without_creating_a_window(self):
        overlay = object.__new__(record_overlay.StatusOverlay)
        overlay.messages = queue.Queue()
        overlay.messages.put(None)
        gui = record_overlay.win32gui
        # Keep the real GDI font constructor: pywin32 requires a LOGFONT object.
        with patch.object(gui, 'CreateWindowEx', return_value=100), \
                patch.object(gui, 'SendMessage'), patch.object(gui, 'SetLayeredWindowAttributes'), \
                patch.object(gui, 'PumpWaitingMessages'), patch.object(gui, 'IsWindow', return_value=True), \
                patch.object(gui, 'DestroyWindow') as destroy, patch('builtins.print') as output:
            overlay._run()
        output.assert_not_called()
        destroy.assert_called_once_with(100)


if __name__ == '__main__':
    unittest.main()
