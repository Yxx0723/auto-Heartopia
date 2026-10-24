from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from tests.unit.test_cooking_flow import Kitchen
from tests.unit.test_default_cooking import inventory as stock, view
from utils.core.cooking import CookingSession, Observation
from utils.flows.cooking import run_cooking
from utils.drivers.timeline_hotkeys import PlaybackControls


def test_default_opens_stove_then_cooks_and_confirms_actual_dish():
    k = Kitchen(); k.stage = 'interact'
    controls = SimpleNamespace(snapshot=k.controls, pause=lambda: None)
    def observe(image):
        return Observation(k.stage, (20, 20), recipe_view=view())
    def inventory():
        data = stock()
        if k.count:
            data['counts'][99] = 1
            data['items'].append({'item_id': 99, 'prepared': True})
        return data
    def execute(event):
        k.clicks.append(event)
        k.stage = {'interact': 'menu', 'menu': 'stir', 'stir': 'done', 'done': 'interact'}[k.stage]
        if len(k.clicks) == 4: k.count = 1
    k.execute = execute
    assert run_cooking(CookingSession(None, 1, default_mode=True), k, controls, k,
                       observe, inventory, k, lambda *a, **kw: None)
    assert len(k.clicks) == 4


def test_changed_materials_immediately_before_dispatch_cannot_start():
    k = Kitchen(); n = 0
    def observe(image):
        nonlocal n
        n += 1
        v = view()
        v['signature'] = ('changed' if n == 3 else 'original',)
        return Observation('menu', (20, 20), recipe_view=v)
    controls = SimpleNamespace(snapshot=k.controls, pause=lambda: None)
    assert not run_cooking(CookingSession(None, 1, default_mode=True), k, controls, k,
                           observe, stock, k, lambda *a, **kw: None)
    assert not k.clicks


def test_auto_resume_requires_bound_foreground_and_matching_size():
    window = Mock(); window.size.return_value = (1366, 768)
    control = PlaybackControls(window, (1366, 768), Mock())
    window.foreground.return_value = False
    assert not control.resume() and not control.running
    window.foreground.return_value = True
    assert control.resume() and control.running
    control.stopped = True
    assert not control.resume()


def test_material_locked_after_menu_validation_cannot_be_consumed():
    k = Kitchen(); calls = 0
    def inventory():
        nonlocal calls
        calls += 1
        bag = stock()
        if calls >= 3: bag['items'][0]['locked'] = True
        return bag
    controls = SimpleNamespace(snapshot=k.controls, pause=lambda: None)
    with pytest.raises(ValueError, match='锁定'):
        run_cooking(CookingSession(None, 1, default_mode=True), k, controls, k,
                    lambda image: Observation('menu', (20, 20), recipe_view=view()),
                    inventory, k, lambda *a, **kw: None)
    assert not k.clicks and not k.keys


def test_scene_change_stops_even_while_visual_prompt_is_unknown():
    k = Kitchen()
    s = CookingSession(None, 1, default_mode=True)
    def observe(image):
        v = view()
        if k.clicks: v['scope'] = 'another-scene'
        return Observation('unknown' if k.clicks else 'menu', (20, 20), recipe_view=v)
    controls = SimpleNamespace(snapshot=k.controls, pause=lambda: None)
    assert not run_cooking(s, k, controls, k, observe, stock, k, lambda *a, **kw: None)
    assert s.state == 'invalid' and len(k.clicks) == 1
