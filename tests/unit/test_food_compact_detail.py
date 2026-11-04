"""Actual compact Eat/Share layout still requires an Eat label and one star."""
from pathlib import Path
from unittest.mock import Mock
from PIL import Image,ImageDraw
import pytest
from utils.vision.food_menu import FruitMenu

ROOT=Path(__file__).resolve().parents[2]/'webui/coordinate-monitor'
FIXTURE=Path(__file__).with_name('fixtures')/'backpack_compact_egg_detail_1366.png'


def layout():
    menu=FruitMenu(ROOT/'food_bag_marker.png');im=Image.open(FIXTURE)
    bag=menu.bag_in(menu.frame(im));assert bag and bag.get('search_results')
    s=bag['scale'];cell=dict(x=bag['x']-211*s,y=bag['y']+46*s,scale=s,food='温泉蛋')
    return menu,im,cell


def test_compact_eat_action_and_one_star_are_confirmed():
    menu,im,cell=layout();target=menu.ready_food(im,'温泉蛋',cell)
    assert target and target['x']==pytest.approx(591,abs=4) and target['y']==pytest.approx(191,abs=4)
    assert menu.detail_stars(im,target)==1


def test_no_selected_cell_is_not_an_eat_action():
    menu,im,cell=layout();im=im.copy();im.paste('#303030',(853,123,945,215))
    assert menu.ready_food(im,'温泉蛋',cell) is None


def test_share_label_is_not_a_replacement_for_eat():
    menu,im,cell=layout();im=im.copy();im.paste('#22cfc7',(554,172,630,211))
    assert menu.ready_food(im,'温泉蛋',cell) is None


def test_small_cyan_patch_is_not_a_complete_compact_button():
    menu,_,cell=layout();im=Image.new('RGB',(1366,768),'white')
    ImageDraw.Draw(im).rectangle((576,184,605,198),fill='#22cfc7')
    assert not menu.is_food_button(menu.frame(im),dict(x=591.,y=191.,scale=cell['scale']),[cell])


@pytest.mark.parametrize('missing_header', [False, True])
def test_compact_quality_rejects_unexplained_gold_or_missing_header(missing_header):
    menu,im,cell=layout();im=im.copy()
    if missing_header:
        im.paste('#303030',(513,14,785,88))
    else:
        ImageDraw.Draw(im).rectangle((690,68,700,79),fill='#f6d73f')
    target=menu.ready_food(im,'温泉蛋',cell);assert target
    assert menu.detail_stars(im,target) is None


def test_compact_confirmation_requires_two_frames_and_owned_query():
    menu,im,cell=layout();window=Mock();window.snapshot.return_value=im
    target=menu.confirmed_cell(window,'温泉蛋',cell,Mock(),Mock(),lambda:'温泉蛋',star=1)
    assert target and window.snapshot.call_count==2


def test_compact_confirmation_does_not_accept_different_star():
    menu,im,cell=layout();window=Mock();window.snapshot.return_value=im
    assert menu.confirmed_cell(window,'温泉蛋',cell,Mock(),Mock(),lambda:'温泉蛋',star=2) is None
