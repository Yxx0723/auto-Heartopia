"""Batch material read: retry only the unconfirmed snapshot, not a real shortage."""

import pytest

from utils.flows import cooking_batch as batch


RECIPE = {
    'recipe_id': 45561, 'product_id': 45561, 'name': '灯笼草莓热可可',
    'status': 'confirmed',
    'ingredients': [{'name': 'x', 'quantity': 1, 'allowed_item_ids': [1]}],
}


def inventory(count=5, product=0):
    return {'scope': 'bag', 'counts': {1: count, 45561: product},
            'selectable_counts': {1: count, 45561: product}}


class Clock:
    def __init__(self):
        self.monotonic = 0.0
        self.slept = 0.0
        self.calls = []

    def sleep(self, seconds):
        self.slept += seconds
        self.calls.append(seconds)
        self.monotonic += seconds


class Device:
    def release_all(self):
        pass


class Controls:
    def snapshot(self):
        return True, False


class Window:
    def size(self):
        return (200, 100)

    def foreground(self):
        return True


class Memory:
    """Feed a scripted sequence, then a live inventory that can gain products."""

    def __init__(self, snapshots, live=None):
        self.snapshots = list(snapshots)
        self.live = inventory() if live is None else live
        self.reads = 0

    def cooking_inventory_snapshot(self, max_star=1):
        if self.reads < len(self.snapshots):
            value = self.snapshots[self.reads]
        else:
            value = self.live
        self.reads += 1
        return value

    def status(self):
        return {'stove_identity': [1], 'scope': 's', 'local_net_id': 1,
                'status': 0, 'status_started': 0, 'food_id': 0}


class Recipe:
    def snapshot(self):
        return RECIPE


class Native:
    def __init__(self, memory):
        self.memory = memory
        self.recipe = Recipe()

    def snapshot(self):
        return {'stove_identity': [1], 'open': True, 'selected_recipe': RECIPE}

    def bind(self, identity):
        pass

    def state(self):
        return self.memory.status()


def _finish_pot(memory):
    memory.live = inventory(product=1)
    return {
        'state': 'finished', 'collect_reserved': True, 'stove_identity': [1],
        'recipe_id': 45561, 'product_id': 45561, 'product_delta': 1,
        'confirmed_products': 1, 'pot_id': 'p1',
    }


def run_batch(memory, clock, tmp_path):
    reports = []
    result = batch.run_batch(
        target=1, recipe=RECIPE, memory=memory, window=Window(), controls=Controls(),
        device=Device(), clock=clock, report=lambda state, message, **x: reports.append((state, message, x)),
        directory=tmp_path,
        entry_runner=lambda **k: {'state': 'MENU_OPEN'},
        selection_runner=lambda **k: None,
        pot_runner=lambda **k: _finish_pot(memory),
    )
    return result, reports


def test_unconfirmed_then_confirmed_retries_and_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(batch, 'SinglePotReader', Native)
    monkeypatch.setattr(batch, 'MATERIAL_CONFIRM_SECONDS', .5)
    memory = Memory([{}, inventory()])
    clock = Clock()
    result, reports = run_batch(memory, clock, tmp_path)
    assert result['completed_pots'] == 1
    assert memory.reads >= 2
    assert any(state == 'material_unconfirmed' for state, _, _ in reports)
    assert all(call <= .5 for call in clock.calls)


def test_real_shortage_fails_immediately_without_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(batch, 'SinglePotReader', Native)
    short = inventory(0)
    memory = Memory([short])
    clock = Clock()
    with pytest.raises(ValueError, match='当前锅材料不足'):
        run_batch(memory, clock, tmp_path)
    assert memory.reads == 1
    assert clock.calls == []


def test_persistent_unconfirmed_fails_after_bounded_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(batch, 'SinglePotReader', Native)
    monkeypatch.setattr(batch, 'MATERIAL_CONFIRM_ATTEMPTS', 3)
    monkeypatch.setattr(batch, 'MATERIAL_CONFIRM_SECONDS', .1)
    memory = Memory([{}], live={})
    clock = Clock()
    with pytest.raises(ValueError, match='背包材料快照连续未确认'):
        run_batch(memory, clock, tmp_path)
    assert memory.reads == 3
    assert len(clock.calls) == 2
