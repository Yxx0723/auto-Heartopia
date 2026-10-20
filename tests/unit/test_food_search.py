"""Search must precede fruit selection; real captures also contain recipes."""
from pathlib import Path
from unittest.mock import Mock
import ctypes
import pytest
from PIL import Image
from utils.vision.food_menu import FruitMenu
from utils.drivers.town_window import TimelineInput
from utils.drivers.windows_input import WindowsInput, Input

ROOT = Path(__file__).resolve().parents[2]/'webui/coordinate-monitor'
FIXTURES = Path(__file__).with_name('fixtures')


@pytest.mark.parametrize('width', [778, 1024, 1366, 1920])
def test_actual_search_modal_and_search_results_at_multiple_resolutions(width):
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    scale = width/1024
    opened = Image.open(FIXTURES/'backpack_search_open.png').resize((width, round(768*scale)))
    dialog = menu.search_dialog(opened)
    assert dialog and dialog['x'] == pytest.approx(458*scale, abs=4)
    assert dialog['submit']['x'] == pytest.approx(670*scale, abs=4)
    assert dialog['submit']['y'] == pytest.approx(321*scale, abs=4)
    results = Image.open(FIXTURES/'backpack_search_results.png').resize((width, round(768*scale)))
    assert menu.search_dialog(results) is None
    frame = menu.frame(results);bag = menu.bag_in(frame)
    assert bag and bag['search_results']
    fruits = menu.available(frame, bag)
    assert fruits and {f['food'] for f in fruits} == {'苹果'}  # Recipe cards are not fruit.
    close = menu.close_target(results)
    assert close and close['x'] == pytest.approx(973*scale, abs=4)
    assert close['y'] == pytest.approx(24*scale, abs=4)


@pytest.mark.parametrize('width', [778, 1024, 1366, 1920])
def test_actual_filtered_apple_detail_confirms_selected_fruit_and_eat_button(width):
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    scale = width/1024
    picture = Image.open(FIXTURES/'backpack_search_detail.png').resize((width, round(768*scale)))
    target = menu.ready(picture)
    assert target and target['food'] == '苹果'
    assert target['x'] == pytest.approx(423*scale, abs=4)
    assert target['y'] == pytest.approx(164*scale, abs=4)


def test_search_header_without_its_search_button_is_not_an_input_target():
    picture = Image.open(FIXTURES/'backpack_search_open.png').copy()
    picture.paste('#353535', (615, 295, 730, 345))
    assert FruitMenu(ROOT/'food_bag_marker.png').search_dialog(picture) is None


def search_game(available=('苹果',)):
    state = {'page': 'dialog', 'query': '', 'selected': False, 'queries': [], 'eaten': False}
    menu = FruitMenu.__new__(FruitMenu)
    window, device = Mock(), Mock()
    window.snapshot.side_effect = lambda: dict(state)
    menu.frame = lambda picture: picture
    menu.search_dialog = lambda picture: ({'x': 1, 'y': 1, 'submit': {'x': 2, 'y': 2},
                                          'close': {'x': 8, 'y': 8}} if picture['page'] == 'dialog' else None)
    menu.end_search_target = lambda picture: {'x': 3, 'y': 3} if picture['page'] == 'results' else None
    menu.bag_in = lambda picture: {'x': 20, 'y': 20, 'scale': 1., 'search_results': True} if picture['page'] == 'results' else None
    menu.locate = lambda picture: menu.bag_in(picture)
    menu.available = lambda picture, bag: ([{'x': 4, 'y': 4, 'food': state['query']}] if state['query'] in available else [])
    menu.ready = lambda picture: {'x': 5, 'y': 5, 'food': state['query']} if state['selected'] else None
    def click(target, device, check, wait):
        check()
        if target['x'] == 2:
            state['page'] = 'results';state['queries'].append(state['query'])
        elif target['x'] == 3:
            state['page'] = 'normal'
        elif target['x'] == 4:
            state['selected'] = True
        elif target['x'] == 5:
            state['eaten'] = True
        elif target['x'] == 7:
            state['page'] = 'dialog'
    menu.use = click
    original_open = menu.open_search
    def open_search(*args):
        if state['page'] == 'results':state['page'] = 'dialog'
        return original_open(*args)
    menu.open_search = open_search
    device.type_text.side_effect = lambda text, check, wait: state.update(query=text, selected=False)
    return menu, window, device, state


def test_search_falls_back_in_priority_order_without_eating_a_recipe_or_consuming_in_prepare():
    menu, window, device, state = search_game(available=('树莓',))
    target = menu.prepare(window, Mock(), Mock(), Mock(), device)
    assert state['queries'] == ['苹果', '蓝莓', '树莓']
    assert target['food'] == '树莓' and not state['eaten']
    assert [c.args[0]['type'] for c in device.execute.call_args_list].count('key_up') == 3


def test_four_empty_searches_stop_without_consuming():
    menu, window, device, state = search_game(available=())
    with pytest.raises(ValueError, match='没有可用'):
        menu.prepare(window, Mock(), Mock(), Mock(), device)
    assert state['queries'] == ['苹果', '蓝莓', '树莓', '橘子'] and not state['eaten']


def test_unconfirmed_search_modal_never_types_or_clicks():
    menu = FruitMenu(ROOT/'food_bag_marker.png');window, device = Mock(), Mock()
    window.snapshot.return_value = Image.new('RGB', (778, 437), '#306230')
    with pytest.raises(ValueError, match='未确认'):
        menu.prepare(window, Mock(), Mock(), Mock(), device)
    device.type_text.assert_not_called();device.execute.assert_not_called()


def test_failed_text_input_always_releases_the_select_all_modifier():
    menu, window, device, state = search_game()
    def fail(*args):raise OSError('input unavailable')
    device.type_text.side_effect = fail
    with pytest.raises(OSError):menu.prepare(window, Mock(), Mock(), Mock(), device)
    assert device.execute.call_args_list[-1].args[0] == {'type': 'key_up', 'key': 'ctrl_l'}
    assert not state['queries'] and not state['eaten']


def test_unicode_search_uses_checked_native_unicode_events_and_releases_each_unit():
    events = []
    def send(count, ptr, size):
        item = ctypes.cast(ptr, ctypes.POINTER(Input)).contents
        events.append((item.type, item.ki.wVk, item.ki.wScan, item.ki.dwFlags))
        return 1
    device = TimelineInput(native=WindowsInput(send=send))
    device.type_text('苹果', Mock(), Mock())
    assert events == [(1, 0, ord(c), flag) for c in '苹果' for flag in (4, 6)]
    assert not device.keys


def test_interruption_between_characters_stops_text_and_keeps_previous_unit_released():
    native = Mock();device = TimelineInput(native=native)
    check = Mock(side_effect=[None, ValueError('paused')])
    with pytest.raises(ValueError, match='paused'):device.type_text('苹果', check, Mock())
    assert [c.args for c in native.unicode.call_args_list] == [(ord('苹'), True), (ord('苹'), False)]


def test_search_result_close_clears_results_before_closing_backpack():
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    results = Image.open(FIXTURES/'backpack_search_results.png')
    from tests.unit.test_food_menu import frame
    normal = frame()
    closed = Image.new('RGB', (778, 437), '#306230')
    pages = [results, normal, closed]
    stage = [0];window, device = Mock(), Mock()
    window.snapshot.side_effect = lambda: pages[stage[0]]
    def send(event):
        if event['type'] == 'mouse_up':stage[0] += 1
    device.execute.side_effect = send
    assert menu.close_menu(window, Mock(), Mock(), Mock(), device)
    assert stage[0] == 2


def test_search_dialog_close_handles_the_modal_then_closes_backpack():
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    modal = Image.open(FIXTURES/'backpack_search_open.png')
    from tests.unit.test_food_menu import frame
    pages = [modal, frame(), Image.new('RGB', (778, 437), '#306230')]
    stage = [0];window, device = Mock(), Mock()
    window.snapshot.side_effect = lambda: pages[stage[0]]
    def send(event):
        if event['type'] == 'mouse_up':stage[0] += 1
    device.execute.side_effect = send
    assert menu.close_menu(window, Mock(), Mock(), Mock(), device)
    assert stage[0] == 2
