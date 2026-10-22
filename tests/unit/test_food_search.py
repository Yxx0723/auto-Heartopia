"""Search must precede fruit selection; real captures also contain recipes."""
import sys
import pytest
if sys.platform != 'win32':
    pytest.skip('Windows native input/window contract', allow_module_level=True)

from pathlib import Path
from unittest.mock import Mock
import ctypes
from PIL import Image
from utils.vision.food_menu import FruitMenu

def stock(*names):
    return [{'name':n, 'count':5, 'edible':True} for n in names]

from utils.drivers.town_window import TimelineInput
from utils.drivers.windows_input import WindowsInput, Input

ROOT = Path(__file__).resolve().parents[2]/'resources/monitor'
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
    menu.detail_stars = lambda picture, button: state.get('star', 0)
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


def test_inventory_candidates_are_searched_without_blind_queries_or_consuming_in_prepare():
    menu, window, device, state = search_game(available=('树莓',))
    target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=stock('树莓'))
    assert state['queries'] == ['树莓']
    assert target['food'] == '树莓' and not state['eaten']
    assert [c.args[0]['type'] for c in device.execute.call_args_list].count('key_up') == 1


def test_empty_inventory_never_opens_search_or_consumes():
    menu, window, device, state = search_game(available=())
    with pytest.raises(ValueError, match='没有确认可食用'):
        menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=[])
    assert state['queries'] == [] and not state['eaten']


def test_unconfirmed_search_modal_never_types_or_clicks():
    menu = FruitMenu(ROOT/'food_bag_marker.png');window, device = Mock(), Mock()
    window.snapshot.return_value = Image.new('RGB', (778, 437), '#306230')
    with pytest.raises(ValueError, match='未确认'):
        menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=stock('苹果'))
    device.type_text.assert_not_called();device.execute.assert_not_called()


def test_failed_text_input_always_releases_the_select_all_modifier():
    menu, window, device, state = search_game()
    def fail(*args):raise OSError('input unavailable')
    device.type_text.side_effect = fail
    with pytest.raises(OSError):menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=stock('苹果'))
    assert device.execute.call_args_list[-1].args[0] == {'type': 'key_up', 'key': 'ctrl_l'}
    assert not state['queries'] and not state['eaten']


def test_search_results_wait_for_the_bag_header_to_finish_loading():
    menu, window, device, state = search_game()
    original = menu.bag_in
    reads = []
    def delayed(picture):
        if picture['page'] == 'results':
            reads.append(True)
            if len(reads) <= 2:
                return None
        return original(picture)
    menu.bag_in = delayed
    target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=stock('苹果'))
    assert target['food'] == '苹果' and len(reads) >= 3 and not state['eaten']


def test_random_selection_uses_only_actual_owned_food_and_no_fixed_fruit_order(monkeypatch):
    menu, window, device, state = search_game(available=('苹果', '蓝莓'))
    monkeypatch.setattr('utils.vision.food_menu.random.sample', lambda names, count: list(reversed(names)))
    target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=stock('苹果', '蓝莓'))
    assert target['food'] == '蓝莓' and state['queries'] == ['蓝莓'] and not state['eaten']


def test_prepared_meals_are_the_only_search_candidates_while_in_stock(monkeypatch):
    menu, window, device, state = search_game(available=('苹果', '蓝莓'))
    # This mock exercises priority independent of icon recognition; the
    # inventory reader separately proves the actual cooked-food type.
    items = stock('苹果', '蓝莓')
    items[1]['prepared'] = True
    items[1]['star'] = 0
    seen = []
    def choose(names, count):
        seen.append(list(names))
        return list(names)
    monkeypatch.setattr('utils.vision.food_menu.random.sample', choose)
    target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=items)
    assert target['food'] == '蓝莓' and seen == [['蓝莓']] and state['queries'] == ['蓝莓']


def test_depleted_or_locked_prepared_meals_fall_back_to_other_food():
    menu, window, device, state = search_game()
    items = [*stock('苹果'), {'name':'温泉蛋', 'edible':True, 'prepared':True, 'count':0},
             {'name':'大厨特制沙拉', 'edible':True, 'prepared':True, 'count':2, 'locked':True}]
    target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=items)
    assert target['food'] == '苹果' and state['queries'] == ['苹果']


def test_unconfirmed_prepared_meal_does_not_fall_through_to_raw_food():
    menu, window, device, state = search_game(available=('苹果',))
    items = stock('苹果', '蓝莓')
    items[1]['prepared'] = True
    items[1]['star'] = 0
    with pytest.raises(ValueError, match='食用按钮未确认'):
        menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=items)
    assert state['queries'] == ['蓝莓'] and not state['eaten']


def test_lowest_star_prepared_food_wins_over_other_dishes_and_raw_food(monkeypatch):
    menu, window, device, state = search_game(available=('苹果', '蓝莓', '树莓'))
    items = stock('苹果', '蓝莓', '树莓')
    items[0].update(prepared=True, star=3)
    items[1].update(prepared=True, star=1)
    state['star'] = 1
    seen = []
    monkeypatch.setattr('utils.vision.food_menu.random.sample', lambda names, count: seen.append(names) or names)
    target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=items)
    assert target['food'] == '蓝莓' and target['star'] == 1
    assert seen == [['蓝莓']] and state['queries'] == ['蓝莓'] and not state['eaten']


def test_equal_low_star_dishes_remain_random_and_depleted_low_star_is_excluded(monkeypatch):
    menu, window, device, state = search_game(available=('苹果', '蓝莓'))
    items = stock('苹果', '蓝莓')
    for item in items:
        item.update(prepared=True, star=2)
    items.append(dict(name='温泉蛋', prepared=True, star=1, count=0, edible=True))
    state['star'] = 2
    seen = []
    monkeypatch.setattr('utils.vision.food_menu.random.sample', lambda names, count: seen.append(names) or list(reversed(names)))
    target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=items)
    assert target['food'] == '蓝莓' and seen == [['苹果', '蓝莓']] and not state['eaten']


@pytest.mark.parametrize('star', [None, -1, 6, True, '1'])
def test_unknown_or_invalid_prepared_quality_retries_without_search_or_fallback(star):
    menu, window, device, state = search_game()
    items = [*stock('苹果'), dict(name='温泉蛋', prepared=True, star=star, count=2, edible=True)]
    with pytest.raises(ValueError, match='星级未确认'):
        menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=items)
    assert state['queries'] == [] and not state['eaten']


@pytest.mark.parametrize('width', [778, 1024, 1366, 1920])
@pytest.mark.parametrize('stars', [1, 2, 3, 5])
def test_actual_detail_header_counts_quality_at_multiple_resolutions(width, stars):
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    picture = Image.open(FIXTURES/'backpack_one_star_egg.png').convert('RGB')
    # The first star is the real user-confirmed one-star egg capture. Extra
    # real glyphs exercise adjacent stars without depending on grid sorting.
    glyph = picture.crop((830, 99, 845, 114))
    for index in range(1, stars):
        picture.paste(glyph, (830+17*index, 99))
    picture = picture.resize((width, round(picture.height*width/picture.width)))
    frame = menu.frame(picture)
    buttons = menu.matches(frame, menu.eat[:, 38:74], menu.scales(frame), minimum=.88)
    assert buttons and menu.detail_stars(picture, buttons[0]) == stars


def test_zero_stars_requires_real_header_and_ambiguous_gold_is_not_zero():
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    picture = Image.open(FIXTURES/'backpack_search_detail.png').convert('RGB')
    frame = menu.frame(picture)
    button = menu.matches(frame, menu.eat[:, 38:74], menu.scales(frame), minimum=.88)[0]
    assert menu.detail_stars(picture, button) == 0
    picture.paste('#f6d73f', (418, 49, 428, 59))
    assert menu.detail_stars(picture, button) is None
    picture.paste('#333333', (322, 12, 524, 80))
    assert menu.detail_stars(picture, button) is None


@pytest.mark.parametrize('observed', [(3, 2, 1), (3, 2, None), (3, 2, 2), (3, 3, 2, 2, 2, 1)])
def test_same_name_high_star_cells_are_skipped_and_only_confirmed_low_star_returns(observed):
    menu, window, device, state = search_game(available=('温泉蛋',))
    class Frame(dict):
        shape = (437, 778, 3)
    menu.frame = lambda p: Frame(p)
    menu.bag_in = lambda p: dict(x=730, y=50, scale=1., search_results=True) if p['page']=='results' else None
    click = menu.use
    def use(target, *args):
        if target.get('food') == '温泉蛋':
            state['selected'] = True
            state['slot'] = round((target['x']-519)/54)+5*round((target['y']-96)/54)
        else:
            click(target, *args)
    menu.use = use
    menu.ready_food = lambda p, food, cell: dict(x=5, y=5, food=food, scale=1.) if state['selected'] else None
    menu.detail_stars = lambda p, b: observed[state['slot']] if state['slot'] < len(observed) else None
    items = [dict(name='温泉蛋', prepared=True, star=star, count=2, edible=True) for star in (3, 2, 1)]
    if observed[-1] == 1:
        target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=items)
        assert target['star'] == 1 and state['slot'] == len(observed)-1
    else:
        with pytest.raises(ValueError, match='未确认'):
            menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=items)
    assert state['queries'] == ['温泉蛋'] and not state['eaten']


def test_generic_owned_food_is_searched_then_prepared_without_requiring_a_fruit_icon():
    menu, window, device, state = search_game(available=('温泉蛋',))
    class Frame(dict):
        shape = (437, 778, 3)
    menu.frame = lambda p: Frame(p)
    menu.bag_in = lambda p: dict(x=730, y=50, scale=1., search_results=True) if p['page']=='results' else None
    click = menu.use
    def use(target, *args):
        if target.get('food') == '温泉蛋':
            state['selected'] = True
        else:
            click(target, *args)
    menu.use = use
    menu.ready_food = lambda p, food, cell: dict(x=5, y=5, food=food) if state['selected'] else None
    target = menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=stock('温泉蛋'),
                          query_check=lambda:state['query'])
    assert target['food'] == '温泉蛋' and state['queries'] == ['温泉蛋'] and not state['eaten']


def test_inventory_zero_locked_and_non_edible_items_never_trigger_search():
    menu, window, device, state = search_game()
    invalid = [{'name':'苹果', 'edible':True, 'count':0},
               {'name':'蓝莓', 'edible':True, 'count':5, 'locked':True},
               {'name':'苹果食谱', 'edible':False, 'count':1}]
    with pytest.raises(ValueError, match='没有确认可食用'):
        menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=invalid)
    assert state['queries'] == [] and not state['eaten']


def test_delayed_search_dialog_waits_for_two_stable_frames_before_typing():
    menu, window, device, state = search_game()
    detect = menu.search_dialog
    count = [0]
    def delayed(picture):
        count[0] += 1
        return None if count[0] < 5 else detect(picture)
    menu.search_dialog = delayed
    dialog = menu.stable_dialog(window, Mock(), Mock())
    assert dialog['submit'] == {'x':2,'y':2} and count[0] == 6
    device.type_text.assert_not_called()


def test_wrong_actual_query_rejects_the_result_before_any_item_is_selected():
    menu, window, device, state = search_game()
    with pytest.raises(ValueError, match='实际搜索文字不符'):
        menu.prepare(window, Mock(), Mock(), Mock(), device, inventory=stock('苹果'), query_check=lambda:'食物')
    assert not state['selected'] and not state['eaten']


@pytest.mark.parametrize('width', [778, 1366])
def test_inventory_confirmed_generic_food_requires_selected_cell_and_actual_eat_button(width):
    menu = FruitMenu(ROOT/'food_bag_marker.png')
    scale = width/1024
    image = Image.open(FIXTURES/'backpack_search_detail.png').resize((width, round(768*scale)))
    pixels = menu.frame(image)
    bag = menu.bag_in(pixels)
    cell = dict(x=bag['x']-211*bag['scale'], y=bag['y']+46*bag['scale'], scale=bag['scale'])
    # Reuse the captured card geometry to exercise the generic-food branch;
    # inventory ownership/name are validated separately, not inferred here.
    target = menu.ready_food(image, '温泉蛋', cell)
    assert target and target['food'] == '温泉蛋'
    no_selection = Image.open(FIXTURES/'backpack_search_results.png').resize(image.size)
    assert menu.ready_food(no_selection, '温泉蛋', cell) is None
    image.paste('#333333', (round(350*scale), round(145*scale), round(480*scale), round(185*scale)))
    assert menu.ready_food(image, '温泉蛋', cell) is None


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
