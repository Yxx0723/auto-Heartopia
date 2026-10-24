"""Template safety, recipe guards and movable world prompts; no desktop input."""
import base64
import io
import json
from pathlib import Path

from PIL import Image
import numpy as np
import pytest

from utils.vision.cooking_menu import CookingMenu, REQUIRED


PROFILE = Path(__file__).resolve().parents[2]/'webui/coordinate-monitor/cooking-coffee.json'


def scene(*parts):
    profile = json.loads(PROFILE.read_text('utf-8'))
    picture = Image.new('RGB', tuple(profile['client_size']), '#383838')
    for name in parts:
        entry = profile['templates'][name]
        x, y, w, h = entry['rect']
        picture.paste(Image.open(io.BytesIO(base64.b64decode(entry['png']))), (x, y))
    return profile, picture


def test_menu_requires_recipe_name_and_all_four_materials():
    p, image = scene('menu', 'recipe', 'materials', 'start')
    assert CookingMenu(p).observe(image).stage == 'menu'
    for missing in ('recipe', 'materials', 'start'):
        p, image = scene(*[n for n in ('menu', 'recipe', 'materials', 'start') if n != missing])
        assert CookingMenu(p).observe(image).stage == 'unknown'


@pytest.mark.parametrize('part,stage', [('cooking', 'cooking'), ('stir', 'stir'),
                                       ('collect', 'done'), ('interact', 'interact')])
def test_separate_world_prompts_are_identified(part, stage):
    p, image = scene(part)
    assert CookingMenu(p).observe(image).stage == stage


def test_two_distinct_stir_targets_are_ambiguous():
    p, image = scene('stir')
    entry = p['templates']['stir']
    template = Image.open(io.BytesIO(base64.b64decode(entry['png'])))
    image.paste(template, (entry['rect'][0]+130, entry['rect'][1]))
    assert CookingMenu(p).observe(image).stage == 'unknown'


def test_resolution_change_never_uses_old_coordinates():
    p, image = scene('stir')
    with pytest.raises(ValueError, match='尺寸'):
        CookingMenu(p).observe(image.resize((1000, 700)))


@pytest.mark.parametrize('missing', REQUIRED)
def test_incomplete_calibration_cannot_start(missing):
    p, _ = scene()
    del p['templates'][missing]
    with pytest.raises(ValueError, match='标定'):
        CookingMenu(p)
