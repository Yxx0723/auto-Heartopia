from PIL import Image
from types import SimpleNamespace

import pytest

from tests.unit.test_cooking import session, stock
from utils.core.cooking import Observation
from utils.flows.cooking import run_cooking


class Kitchen:
    def __init__(self):
        self.time, self.stage, self.count = 0., 'menu', 0
        self.clicks = []
        self.keys = set()
        self.stopped = False
        self.focus = True
        self.release_count = 0

    def monotonic(self): return self.time
    def sleep(self, dt):
        self.time += dt
        assert self.time < 10
    def size(self): return [100, 100]
    def foreground(self): return self.focus
    def snapshot(self): return Image.new('RGB', (100, 100))
    def controls(self): return True, self.stopped
    def observe(self, image): return Observation(self.stage, (20, 20))
    def inventory(self): return stock(self.count)
    def execute(self, event):
        self.clicks.append(event)
        self.keys.add('mouse')
        self.stage = {'menu': 'stir', 'stir': 'done', 'done': 'menu'}[self.stage]
        if len(self.clicks) == 3: self.count = 1
    def release_all(self):
        self.release_count += 1
        self.keys.clear()


def run(k, observe=None):
    controls = SimpleNamespace(snapshot=k.controls, pause=lambda: None)
    return run_cooking(session(1), k, controls, k, observe or k.observe, k.inventory, k,
                       lambda *args, **kwargs: None)


def test_complete_pot_with_fresh_vision_and_actual_receipt():
    k = Kitchen()
    assert run(k)
    assert len(k.clicks) == 3 and not k.keys


def test_f9_during_click_releases_input_and_cannot_create_another_action():
    k = Kitchen()
    original = k.execute
    def execute(event):
        original(event); k.stopped = True
    k.execute = execute
    assert not run(k)
    assert len(k.clicks) == 1 and not k.keys


def test_exception_releases_input_and_does_not_retry_reserved_action():
    k = Kitchen()
    def execute(event):
        k.keys.add('mouse')
        raise OSError('input failed')
    k.execute = execute
    with pytest.raises(OSError): run(k)
    assert not k.keys


def test_out_of_window_target_cannot_click():
    k = Kitchen()
    with pytest.raises(ValueError, match='客户区'):
        run(k, lambda image: Observation('menu', (101, 20)))
    assert not k.clicks and not k.keys
