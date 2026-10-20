from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from utils.vision.axe_pick import AxePickMenu, LazyToolSelector

TEMPLATE = Path(__file__).resolve().parents[2]/'webui/coordinate-monitor/axe_pick_icon.png'


def frame(scale=1., held=False, neighbour=True, copies=1):
    picture = Image.new('RGB', (round(1024*scale), round(768*scale)), '#306230')
    axe = Image.open(TEMPLATE).convert('RGB')
    if not held:
        data = np.asarray(axe).copy()
        data[data.max(axis=2) >= 200] = (207, 198, 184)
        axe = Image.fromarray(data)
    marker = Image.open(TEMPLATE.with_name('tool_tablet_marker.png')).convert('RGB')
    size = round(45*scale), round(46*scale)
    axe, marker = axe.resize(size), marker.resize(size)
    for x, y in [(664, 575)]+([(800, 620)] if copies == 2 else []):
        picture.paste(axe, (round(x*scale), round(y*scale)))
        if neighbour:
            picture.paste(marker, (round((x+68)*scale), round((y+1)*scale)))
    return picture


@pytest.mark.parametrize('scale', [1., 1.25, 1.5, 2.])
@pytest.mark.parametrize('held', [False, True])
def test_icon_recognition_ignores_held_highlight_and_uses_current_game_size(scale, held):
    target = AxePickMenu(TEMPLATE).locate(frame(scale, held))
    assert target is not None
    assert target['x'] == pytest.approx(686*scale, abs=2)
    assert target['y'] == pytest.approx(598*scale, abs=2)


def test_axe_alone_without_menu_neighbour_is_not_a_click_target():
    menu = AxePickMenu(TEMPLATE)
    assert menu.locate(frame(neighbour=False)) is None
    assert menu.locate(Image.new('RGB', (1024, 768), 'white')) is None


@pytest.mark.parametrize('basis', ['width', 'height', 'blend'])
@pytest.mark.parametrize('size', [(778, 438), (1280, 720), (1920, 1080)])
def test_widescreen_tool_icons_do_not_assume_four_by_three_height_scaling(size, basis):
    width, height = size
    xscale, yscale = width/1024., height/768.
    scale = {'width': xscale, 'height': yscale, 'blend': (xscale*yscale)**.5}[basis]
    picture = Image.new('RGB', size, '#306230')
    left, top = round(width*.65), round(height*.70)
    icon_size = round(45*scale), round(46*scale)
    for path, dx, dy in [(TEMPLATE, 0, 0), (TEMPLATE.with_name('tool_tablet_marker.png'), 68, 1)]:
        picture.paste(Image.open(path).convert('RGB').resize(icon_size),
                      (left+round(dx*scale), top+round(dy*scale)))
    target = AxePickMenu(TEMPLATE).locate(picture)
    assert target is not None
    assert target['x'] == pytest.approx(left+22*scale, abs=2)
    assert target['y'] == pytest.approx(top+23*scale, abs=2)


def test_real_small_widescreen_icons_and_neighbour_are_required_together():
    # Cropped icon cells only, captured from the failing 778x437 game client.
    icons = Image.open(Path(__file__).with_name('fixtures')/'axe_pick_widescreen.png').convert('RGB')
    picture = Image.new('RGB', (778, 437), '#306230')
    picture.paste(icons, (499, 282))
    menu = AxePickMenu(TEMPLATE)
    target = menu.locate(picture)
    assert target is not None
    assert target['x'] == pytest.approx(523, abs=2)
    assert target['y'] == pytest.approx(307, abs=2)
    picture.paste('#306230', (550, 282, 602, 334))
    assert menu.locate(picture) is None


def test_ambiguous_menu_icons_are_rejected():
    with pytest.raises(ValueError, match='歧义'):
        AxePickMenu(TEMPLATE).locate(frame(copies=2))


def test_prepare_opens_with_t_and_observes_twice_without_clicking_tool():
    menu, window, tap = AxePickMenu(TEMPLATE), Mock(), Mock()
    window.snapshot.side_effect = [Image.new('RGB', (1024, 768)), frame(), frame()]
    target = menu.prepare(window, tap, Mock(), Mock())
    assert target['x'] == 686
    tap.assert_called_once_with('t')
    assert window.snapshot.call_count == 3


def test_lazy_selector_does_not_load_assets_before_actual_playback_needs_it():
    factory = Mock()
    selector = LazyToolSelector(TEMPLATE, factory)
    factory.assert_not_called()
    selector.prepare(1, 2, 3, 4)
    selector.close_menu(1, 2, 3, 4)
    factory.assert_called_once_with(TEMPLATE)


@pytest.mark.parametrize('size', [(1366, 768), (1920, 1080)])
def test_scaled_current_menu_maps_verified_tool_back_to_client_coordinates(size):
    crop = Image.open(Path(__file__).with_name('fixtures')/'repair_menu_current.png').convert('RGB')
    scale = size[0]/778.
    crop = crop.resize((round(crop.width*scale), round(crop.height*scale)))
    picture = Image.new('RGB', size, '#306230')
    left, top = round(492*scale), round(235*scale)
    picture.paste(crop, (left, top))
    menu = AxePickMenu(TEMPLATE)
    target = menu.locate(picture)
    assert target is not None
    assert target['x'] == pytest.approx(left+30*scale, abs=4)
    assert target['y'] == pytest.approx(top+72*scale, abs=4)
    picture.paste('#306230', (left+round(62*scale), top+round(40*scale),
                             left+crop.width, top+crop.height))
    assert menu.locate(picture) is None
