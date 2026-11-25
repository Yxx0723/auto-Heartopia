from pathlib import Path
import tempfile
from unittest.mock import Mock

import pytest

pytest.importorskip('win32gui')
from tools import fishing_auto_run as live


class FakeMenu:
    """Stand-in for the T-panel matcher; records the click it authorised."""
    label = '鱼竿'

    def __init__(self, target=None, error=None):
        self.target, self.error = target, error
        self.used = []

    def prepare(self, window, tap, wait, check):
        check()
        if self.error is not None:
            raise self.error
        return dict(self.target or {'x': 700, 'y': 640, 'score': 1.})

    def use(self, target, device, check, wait):
        check()
        self.used.append(dict(target))

    def close_menu(self, window, tap, check, wait):
        return None


def runner_with(menu):
    runner = live.FishingRunner.__new__(live.FishingRunner)
    runner.profile = Path(tempfile.gettempdir())/'fishing-equip-fixture'
    runner.input = Mock()
    runner.device = Mock()
    runner.window = Mock()
    runner.log = Mock()
    runner.tap_key = Mock()
    runner.close_panel_if_open = Mock()
    runner.tool_panel_open = False
    runner.equip_attempts = 0
    runner.rod_icon_menu = menu
    runner.panel_menu_cache = None
    return runner


def test_equip_clicks_the_located_rod_tile_and_confirms_through_memory():
    menu = FakeMenu()
    runner = runner_with(menu)
    runner.bind_rod = Mock(return_value=(0, True, {'stage': 0}))

    assert runner.equip_rod() is True
    assert menu.used == [{'x': 700, 'y': 640, 'score': 1.}]
    runner.bind_rod.assert_called_once_with()
    # The panel must be put away before any cast click is authorised.
    runner.close_panel_if_open.assert_called_once_with()


def test_equip_reports_failure_when_memory_still_shows_no_rod(monkeypatch):
    monkeypatch.setattr(live.policy, 'FISHING_EQUIP_CONFIRM_SECONDS', 0.)
    menu = FakeMenu()
    runner = runner_with(menu)
    runner.bind_rod = Mock(side_effect=live.BindingError('NO_TOOL', '当前工具：未装备'))

    assert runner.equip_rod() is False
    runner.close_panel_if_open.assert_called_once_with()


def test_equip_closes_the_panel_even_when_the_icon_cannot_be_located():
    menu = FakeMenu(error=ValueError('未识别到 T 面板的鱼竿'))
    runner = runner_with(menu)
    runner.bind_rod = Mock()

    assert runner.equip_rod() is False
    runner.bind_rod.assert_not_called()
    runner.close_panel_if_open.assert_called_once_with()


def test_equip_is_bounded_by_the_attempt_threshold(monkeypatch):
    monkeypatch.setattr(live.policy, 'FISHING_EQUIP_CONFIRM_SECONDS', 0.)
    menu = FakeMenu()
    runner = runner_with(menu)
    runner.bind_rod = Mock(side_effect=live.BindingError('NO_TOOL', '当前工具：未装备'))

    for _ in range(live.policy.FISHING_EQUIP_ATTEMPTS):
        assert runner.equip_rod() is False
    assert runner.equip_rod() is False
    assert runner.equip_attempts == live.policy.FISHING_EQUIP_ATTEMPTS
    assert len(menu.used) == live.policy.FISHING_EQUIP_ATTEMPTS


def test_missing_artwork_keeps_the_manual_prompt_without_any_click(monkeypatch):
    runner = runner_with(None)
    runner.bind_rod = Mock()
    monkeypatch.setattr(live, 'rod_template', lambda resources: None)
    # Keep the injected fake from being reused when the template is absent.
    runner.rod_icon_menu = None

    assert runner.equip_rod() is False
    runner.bind_rod.assert_not_called()
    runner.close_panel_if_open.assert_not_called()
    runner.device.execute.assert_not_called()
