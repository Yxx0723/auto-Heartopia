from pathlib import Path
from PIL import Image
import pytest
from utils.vision.food_menu import FruitMenu

ROOT=Path(__file__).resolve().parents[2]/'webui/coordinate-monitor'
FIXTURE=Path(__file__).with_name('fixtures')/'food_results_cocoa_1366.png'


def test_live_cocoa_result_uses_independent_end_control_scale():
    menu=FruitMenu(ROOT/'food_bag_marker.png');picture=Image.open(FIXTURE)
    frame=menu.frame(picture);bag=menu.bag_in(frame);end=menu.end_search_target(frame)
    assert bag and bag['search_results'] and menu.search_dialog(picture) is None
    assert bag['scale']==end['scale']
    assert bag['x']-211*bag['scale']==pytest.approx(895,abs=8)
    assert bag['y']+46*bag['scale']==pytest.approx(167,abs=8)


@pytest.mark.parametrize('region',[(1160,695,1335,760),(840,65,940,115)])
def test_title_and_end_control_are_both_required(region):
    menu=FruitMenu(ROOT/'food_bag_marker.png');picture=Image.open(FIXTURE).copy()
    picture.paste('#303030',region)
    assert menu.bag_in(menu.frame(picture)) is None


def test_eat_and_search_exit_wait_for_pointer_arrival_before_press():
    from unittest.mock import Mock
    device=Mock();events=[]
    device.execute.side_effect=lambda event:events.append(event['type'])
    def wait(seconds):
        if seconds==.15:assert events==['cursor_move']
        if seconds==.1:assert events==['cursor_move','mouse_down']
    FruitMenu.use({'x':100,'y':200},device,lambda:None,wait)
    assert events==['cursor_move','mouse_down','mouse_up']
