"""Search title rasterization is insufficient without its input and button."""
from pathlib import Path
from PIL import Image
import pytest
from utils.vision.food_menu import FruitMenu

ROOT=Path(__file__).resolve().parents[2]/'webui/coordinate-monitor'
FIXTURE=Path(__file__).with_name('fixtures')/'backpack_search_modal_1366.png'


def test_current_1366_modal_is_recognized_despite_title_rasterization():
    menu=FruitMenu(ROOT/'food_bag_marker.png');picture=Image.open(FIXTURE)
    dialog=menu.search_dialog(picture)
    assert dialog is not None
    assert dialog['x']==pytest.approx(612,abs=4) and dialog['y']==pytest.approx(300,abs=4)
    assert dialog['submit']['x']==pytest.approx(894,abs=4)


@pytest.mark.parametrize('missing',['input','button','title'])
def test_title_candidate_requires_both_input_and_submit(missing):
    menu=FruitMenu(ROOT/'food_bag_marker.png');picture=Image.open(FIXTURE).copy()
    boxes={'input':(411,274,813,328),'button':(825,274,962,328),'title':(604,178,769,219)}
    picture.paste('#303030',boxes[missing])
    assert menu.search_dialog(picture) is None


def test_low_contrast_unconfirmed_input_is_not_a_typing_target():
    menu=FruitMenu(ROOT/'food_bag_marker.png');picture=Image.open(FIXTURE).copy()
    picture.paste('#eee8df',(411,274,813,328))
    assert menu.search_dialog(picture) is None
