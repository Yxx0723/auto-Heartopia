from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

from utils.vision.food_menu import FruitMenu

ROOT = Path(__file__).resolve().parents[2]/'webui/coordinate-monitor'
FIXTURES = Path(__file__).with_name('fixtures')


def frame(scale=1., selected=True, button=True, bag=True):
    picture = Image.new('RGB', (778, 437), '#306230')
    if bag:
        picture.paste(Image.open(ROOT/'food_bag_marker.png'), (700, 32))
        picture.paste(Image.open(ROOT/'food_bag_close.png'), (729, 8))
    picture.paste(Image.open(FIXTURES/'selected_apple.png') if selected else Image.open(ROOT/'fruit_apple.png'),
                  (703, 118) if selected else (713, 128))
    if button:
        picture.paste(Image.open(ROOT/'food_eat_button.png'), (422, 153))
    picture.paste(Image.open(ROOT/'fruit_blueberry.png'), (551, 179))
    picture.paste(Image.open(ROOT/'fruit_raspberry.png'), (604, 179))
    picture.paste(Image.open(ROOT/'fruit_orange.png'), (497, 179))
    return picture.resize((round(778*scale), round(437*scale)))


@pytest.mark.parametrize('scale', [1., 1.5, 2.])
def test_food_requires_backpack_selected_fruit_and_real_eat_button(scale):
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    target = menu.ready(frame(scale))
    assert target is not None and target['food'] == '苹果'
    assert target['x'] == pytest.approx(478*scale, abs=3)
    assert target['y'] == pytest.approx(162*scale, abs=3)
    for kwargs in ({'selected': False}, {'button': False}, {'bag': False}):
        assert menu.ready(frame(scale, **kwargs)) is None


def test_round_mushroom_is_not_misidentified_as_an_orange():
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    picture = Image.new('RGB', (778, 437), '#306230')
    picture.paste(Image.open(ROOT/'food_bag_marker.png'), (700, 32))
    picture.paste(Image.open(FIXTURES/'food_mushroom.png'), (658, 228))
    pixels = menu.frame(picture)
    assert not menu.available(pixels, menu.bag_in(pixels))


def test_prepare_uses_two_observations_and_does_not_click_an_existing_selection():
    menu, window, device = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock()
    window.snapshot.return_value = frame()
    target = menu.prepare_visible(window, Mock(), Mock(), Mock(), device)
    assert target['food'] == '苹果'
    device.execute.assert_not_called()
    assert window.snapshot.call_count == 2


def test_prepare_does_not_consume_when_the_fruit_click_was_not_registered():
    menu, window, device = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock()
    window.snapshot.return_value = frame(selected=False, button=False)
    with pytest.raises(ValueError, match='未确认'):
        menu.prepare_visible(window, Mock(), Mock(), Mock(), device)
    assert [call.args[0]['type'] for call in device.execute.call_args_list] == ['mouse_down', 'mouse_up']


def test_closed_backpack_opens_with_b_and_closes_by_its_icon_without_other_keys():
    menu, window, device = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock()
    opened, keys = [False], []
    closed = Image.new('RGB', (778, 437), '#306230')
    window.snapshot.side_effect = lambda: frame() if opened[0] else closed

    def tap(key):
        keys.append(key)
        # X is a vehicle action in Heartopia, never a backpack toggle.
        assert key == 'b'
        opened[0] = not opened[0]

    def click(event):
        if event['type'] == 'mouse_up':
            opened[0] = False

    device.execute.side_effect = click

    target = menu.prepare_visible(window, tap, Mock(), Mock(), device)
    assert target['food'] == '苹果' and opened[0]
    device.execute.assert_not_called()
    menu.close_menu(window, tap, Mock(), Mock(), device)
    assert not opened[0] and keys == ['b']
    assert device.execute.call_count == 2
    # Closing an already closed backpack must not reopen it.
    menu.close_menu(window, tap, Mock(), Mock(), device)
    assert keys == ['b'] and device.execute.call_count == 2


def test_failed_backpack_open_does_not_try_vehicle_keys_or_consume_food():
    menu, window, device, tap = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock(), Mock()
    window.snapshot.return_value = Image.new('RGB', (778, 437), '#306230')
    with pytest.raises(ValueError, match='按 B 后未识别到背包'):
        menu.prepare_visible(window, tap, Mock(), Mock(), device)
    tap.assert_called_once_with('b')
    device.execute.assert_not_called()


def test_failed_backpack_close_reports_the_error_without_toggling_other_keys():
    menu, window, tap, device = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock(), Mock()
    window.snapshot.return_value = frame()
    with pytest.raises(ValueError, match='点击关闭后背包仍展开'):
        menu.close_menu(window, tap, Mock(), Mock(), device)
    tap.assert_not_called()
    assert device.execute.call_count == 2


def test_backpack_close_click_dismisses_the_whole_panel_without_keyboard_toggles():
    menu, window, device, tap = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock(), Mock()
    opened = [True]
    menu.locate = Mock(side_effect=lambda picture: {} if opened[0] else None)
    menu.close_target = Mock(return_value={'x': 739.5, 'y': 18.5})
    window.snapshot.return_value = frame()

    def click(event):
        if event['type'] == 'mouse_up':
            opened[0] = False

    device.execute.side_effect = click
    assert menu.close_menu(window, tap, Mock(), Mock(), device)
    assert not opened[0] and device.execute.call_count == 2
    tap.assert_not_called()
    assert not menu.close_menu(window, tap, Mock(), Mock(), device)
    assert device.execute.call_count == 2


def test_backpack_animation_is_allowed_to_finish_without_repeated_clicks():
    menu, window, tap, device = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock(), Mock()
    remaining = [float('inf')]
    menu.locate = Mock(side_effect=lambda picture: {} if remaining[0] > 0 else None)
    menu.close_target = Mock(return_value={'x': 739.5, 'y': 18.5})
    window.snapshot.return_value = frame()
    def click(event):
        if event['type'] == 'mouse_up':
            remaining[0] = .45
    device.execute.side_effect = click

    def wait(seconds):
        remaining[0] -= seconds

    assert menu.close_menu(window, tap, Mock(), wait, device)
    tap.assert_not_called()
    assert device.execute.call_count == 2


@pytest.mark.parametrize('scale', [1., 1.5, 2.])
def test_actual_backpack_close_icon_is_located_inside_the_game_header(scale):
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    with Image.open(FIXTURES/'backpack_close_header.png') as original:
        picture = original.resize((round(778*scale), round(437*scale)))
    target = menu.close_target(picture)
    assert target is not None
    assert target['x'] == pytest.approx(739.5*scale, abs=3)
    assert target['y'] == pytest.approx(18.5*scale, abs=3)


def test_missing_or_misplaced_close_icon_never_clicks_or_sends_keys():
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    for misplaced in (False, True):
        picture = frame()
        picture.paste('#306230', (729, 8, 750, 29))
        if misplaced:
            picture.paste(Image.open(ROOT/'food_bag_close.png'), (729, 268))
        window, device, tap = Mock(), Mock(), Mock()
        window.snapshot.return_value = picture
        with pytest.raises(ValueError, match='未识别到背包关闭按钮'):
            menu.close_menu(window, tap, Mock(), Mock(), device)
        device.execute.assert_not_called()
        tap.assert_not_called()


def test_moving_close_icon_is_not_clicked():
    menu, window, device, tap = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock(), Mock()
    first, second = frame(), frame()
    second.paste('#306230', (729, 8, 750, 29))
    second.paste(Image.open(ROOT/'food_bag_close.png'), (735, 8))
    window.snapshot.side_effect = [first, second]
    with pytest.raises(ValueError, match='关闭按钮位置不稳定'):
        menu.close_menu(window, tap, Mock(), Mock(), device)
    device.execute.assert_not_called()
    tap.assert_not_called()


@pytest.mark.parametrize('scale', [1., 1.5, 2.])
def test_real_bottom_row_apple_card_on_left_with_hover_pointer_is_recognized(scale):
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    with Image.open(FIXTURES/'fruit_card_bottom_left.png') as source:
        picture = source.resize((round(778*scale), round(437*scale)))
    target = menu.ready(picture)
    assert target is not None and target['food'] == '苹果'
    assert target['x'] == pytest.approx(262*scale, abs=3)
    assert target['y'] == pytest.approx(354*scale, abs=3)


def test_hovered_card_already_selected_is_confirmed_without_clicking_the_fruit_again():
    menu, window, device, tap = FruitMenu(ROOT/'food_bag_marker.png'), Mock(), Mock(), Mock()
    with Image.open(FIXTURES/'fruit_card_bottom_left.png') as picture:
        window.snapshot.return_value = picture.copy()
    assert menu.prepare_visible(window, tap, Mock(), Mock(), device)['food'] == '苹果'
    assert window.snapshot.call_count == 2
    tap.assert_not_called()
    device.execute.assert_not_called()


def test_eat_text_without_a_cyan_button_is_not_a_consumption_target():
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    picture = frame(button=False)
    picture.paste(Image.open(ROOT/'food_eat_button.png').crop((38, 0, 74, 18)), (460, 153))
    assert menu.ready(picture) is None


def test_eat_button_unrelated_to_the_selected_inventory_cell_is_rejected():
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    picture = frame(button=False)
    picture.paste(Image.open(ROOT/'food_eat_button.png'), (40, 153))
    assert menu.ready(picture) is None


def test_two_plausible_eat_buttons_remain_ambiguous():
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    picture = frame()
    picture.paste(Image.open(ROOT/'food_eat_button.png'), (422, 213))
    with pytest.raises(ValueError, match='食用按钮位置存在歧义'):
        menu.ready(picture)


@pytest.mark.parametrize('size', [(778, 437), (1366, 768), (1920, 1080)])
def test_current_apple_card_adjacent_to_selected_cell_is_recognized(size):
    source = Image.open(FIXTURES/'apple_detail_adjacent.png').convert('RGB')
    picture = source.resize(size)
    target = FruitMenu(ROOT/'food_bag_marker.png').ready(picture)
    assert target is not None and target['food'] == '苹果'
    assert target['x'] == pytest.approx(1544*size[0]/1920, abs=4)
    assert target['y'] == pytest.approx(820*size[1]/1080, abs=4)


def test_adjacent_card_still_requires_selected_fruit_and_cyan_button():
    source = Image.open(FIXTURES/'apple_detail_adjacent.png').convert('RGB')
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    for missing in ('selected_fruit', 'button_body'):
        picture = source.copy()
        if missing == 'selected_fruit':
            picture.paste('#306230', (1736, 705, 1863, 832))
        else:
            picture.paste('#306230', (1385, 778, 1705, 865))
            picture.paste(source.crop((1494, 795, 1594, 847)), (1494, 795))
        assert menu.ready(picture) is None
