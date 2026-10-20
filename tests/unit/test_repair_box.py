from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from utils.vision.repair_box import RepairBoxMenu


TEMPLATE = Path(__file__).resolve().parents[2]/'webui/coordinate-monitor/repair_box_menu.png'


def frame(scale=1., copies=1):
    picture = Image.new('RGB', (round(1024*scale), round(768*scale)), '#306230')
    template = Image.open(TEMPLATE).convert('RGB')
    template = template.resize((round(43*scale), round(43*scale)))
    marker = Image.open(TEMPLATE.with_name('repair_menu_marker.png')).convert('RGB')
    marker = marker.resize((round(65*scale), round(67*scale)))
    picture.paste(template, (round(653*scale), round(508*scale)))
    picture.paste(marker, (round(655*scale), round(563*scale)))
    if copies == 2:
        picture.paste(template, (round(800*scale), round(600*scale)))
        picture.paste(marker, (round(802*scale), round(655*scale)))
    return picture


@pytest.mark.parametrize('scale', [1., 1.25, 1.5, 2.])
def test_recognizes_relative_position_at_multiple_game_sizes(scale):
    menu = RepairBoxMenu(TEMPLATE)
    target = menu.locate(frame(scale))
    assert abs(target['x']-675*scale) <= 2
    assert abs(target['y']-528*scale) <= 2
    assert target['score'] >= .93


def test_blank_frame_and_red_shapes_are_not_a_repair_menu():
    menu = RepairBoxMenu(TEMPLATE)
    assert menu.locate(Image.new('RGB', (1024, 768), '#e82e22')) is None
    assert menu.locate(Image.new('RGB', (1024, 768), '#faf8f4')) is None


def test_ambiguous_icons_never_produce_a_click_target():
    with pytest.raises(ValueError, match='多个|歧义'):
        RepairBoxMenu(TEMPLATE).locate(frame(copies=2))


def test_opens_with_t_once_and_requires_two_stable_observations():
    menu = RepairBoxMenu(TEMPLATE)
    window = Mock()
    window.snapshot.side_effect = [Image.new('RGB', (1024, 768)), frame(), frame()]
    tap, wait, check = Mock(), Mock(), Mock()
    target = menu.prepare(window, tap, wait, check)
    tap.assert_called_once_with('t')
    assert target['x'] == 675
    assert window.snapshot.call_count == 3


def test_existing_open_menu_is_not_toggled_closed():
    menu = RepairBoxMenu(TEMPLATE)
    window = Mock()
    window.snapshot.return_value = frame()
    tap = Mock()
    menu.prepare(window, tap, Mock(), Mock())
    tap.assert_not_called()


def test_unrecognized_or_unstable_menu_has_no_use_action():
    menu = RepairBoxMenu(TEMPLATE)
    window = Mock()
    blank = Image.new('RGB', (1024, 768))
    for sequence in ([blank, blank], [frame(), blank]):
        window.snapshot.side_effect = sequence
        with pytest.raises(ValueError):
            menu.prepare(window, Mock(), Mock(), Mock())


def test_click_checks_focus_and_always_releases_mouse_after_injection_error():
    device, check = Mock(), Mock()
    device.execute.side_effect = [OSError('failed'), None]
    with pytest.raises(OSError):
        RepairBoxMenu.use({'x': 675, 'y': 528}, device, check, Mock())
    check.assert_called_once()
    assert device.execute.call_args_list[-1].args[0]['type'] == 'mouse_up'


def test_click_holds_for_a_game_frame_and_releases_on_pause_during_wait():
    device, wait = Mock(), Mock(side_effect=ValueError('paused'))
    with pytest.raises(ValueError):
        RepairBoxMenu.use({'x': 675, 'y': 528}, device, Mock(), wait)
    wait.assert_called_once_with(.1)
    assert [c.args[0]['type'] for c in device.execute.call_args_list] == ['mouse_down', 'mouse_up']


def test_close_verifies_panel_disappears_and_does_not_toggle_an_already_closed_panel():
    menu, window, tap = RepairBoxMenu(TEMPLATE), Mock(), Mock()
    blank = Image.new('RGB', (1024, 768))
    window.snapshot.side_effect = [frame(), blank]
    menu.close_menu(window, tap, Mock(), Mock())
    tap.assert_called_once_with('t')
    tap.reset_mock()
    window.snapshot.side_effect = [blank]
    menu.close_menu(window, tap, Mock(), Mock())
    tap.assert_not_called()
    window.snapshot.side_effect = [frame(), frame(), frame()]
    with pytest.raises(ValueError, match='未关闭'):
        menu.close_menu(window, tap, Mock(), Mock())


def test_close_uses_escape_only_if_t_did_not_close_the_recognized_panel():
    menu, window, tap = RepairBoxMenu(TEMPLATE), Mock(), Mock()
    window.snapshot.side_effect = [frame(), frame(), Image.new('RGB', (1024, 768))]
    menu.close_menu(window, tap, Mock(), Mock())
    assert [c.args[0] for c in tap.call_args_list] == ['t', 'esc']


@pytest.mark.parametrize('size', [(778, 437), (1366, 768), (1920, 1080)])
def test_actual_repair_menu_with_current_tool_cells_at_changed_resolutions(size):
    # Real normal/advanced repair boxes and tool/tablet cells from the failing
    # menu. The old whole-cell marker fails despite a strong normal-box match.
    crop = Image.open(Path(__file__).with_name('fixtures')/'repair_menu_current.png').convert('RGB')
    scale = size[0]/778.
    crop = crop.resize((round(crop.width*scale), round(crop.height*scale)))
    picture = Image.new('RGB', size, '#306230')
    left, top = round(492*scale), round(235*scale)
    picture.paste(crop, (left, top))
    target = RepairBoxMenu(TEMPLATE).locate(picture)
    assert target is not None
    assert target['x'] == pytest.approx(left+22*scale, abs=3)
    assert target['y'] == pytest.approx(top+19*scale, abs=3)
    # Retaining only the red advanced box must never select a repair box.
    picture.paste('#306230', (left, top, left+round(38*scale), top+round(37*scale)))
    assert RepairBoxMenu(TEMPLATE).locate(picture) is None


def test_current_normal_box_still_requires_the_independent_tool_menu_context():
    crop = Image.open(Path(__file__).with_name('fixtures')/'repair_menu_current.png').convert('RGB')
    menu = RepairBoxMenu(TEMPLATE)
    for erased in ((0, 40, crop.width, crop.height), (62, 40, crop.width, crop.height)):
        incomplete = crop.copy()
        incomplete.paste('#306230', erased)
        picture = Image.new('RGB', (778, 437), '#306230')
        picture.paste(incomplete, (492, 235))
        assert menu.locate(picture) is None
