"""Visible search-end controls with current font rendering and body checks."""
from pathlib import Path
from unittest.mock import Mock
from PIL import Image
import pytest
from utils.vision.food_menu import FruitMenu

ROOT=Path(__file__).resolve().parents[2]/'webui/coordinate-monitor'
FIXTURE=Path(__file__).with_name('fixtures')/'backpack_results_controls_1366.png'


def test_current_visible_search_end_allows_result_and_grid_recognition():
    menu=FruitMenu(ROOT/'food_bag_marker.png');im=Image.open(FIXTURE);frame=menu.frame(im)
    end=menu.end_search_target(frame)
    assert end and end['x']==pytest.approx(1248,abs=4) and end['y']==pytest.approx(730,abs=4)
    bag=menu.bag_in(frame)
    assert bag and bag.get('search_results')
    assert bag['x']-211*bag['scale']==pytest.approx(899,abs=4)
    assert bag['y']+46*bag['scale']==pytest.approx(170,abs=4)
    assert menu.close_target(im) is not None


def test_covered_end_control_does_not_authorize_selection():
    menu=FruitMenu(ROOT/'food_bag_marker.png');im=Image.open(FIXTURE).copy()
    im.paste('#303030',(1174,704,1321,757));f=menu.frame(im)
    assert menu.end_search_target(f) is None and menu.bag_in(f) is None


@pytest.mark.parametrize('fill',['#303030','#25d4cb','#faf7f2'])
def test_text_candidate_without_orange_body_is_not_a_click(fill):
    menu=FruitMenu(ROOT/'food_bag_marker.png');im=Image.new('RGB',(1366,768),fill)
    menu.matches=Mock(return_value=[dict(x=1248.,y=730.,scale=1.75,score=.87)])
    assert menu.end_search_target(menu.frame(im)) is None


def test_two_distinct_visible_end_candidates_are_ambiguous():
    menu=FruitMenu(ROOT/'food_bag_marker.png');im=Image.new('RGB',(1366,768),(240,196,145))
    menu.matches=Mock(return_value=[dict(x=x,y=730.,scale=1.75,score=.87) for x in (1050.,1248.)])
    with pytest.raises(ValueError,match='歧义'):menu.end_search_target(menu.frame(im))
