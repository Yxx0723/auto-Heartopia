"""Exercise the production wait helper with time advancing inside guard()."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def make_wait(clock, guard, deadline=30.):
    source = Path(__file__).resolve().parents[2] / 'utils/flows/cooking_selection.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    select = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'select_recipe')
    wait = next(n for n in select.body if isinstance(n, ast.FunctionDef) and n.name == 'wait')
    scope = {'clock': clock, 'guard': guard, 'deadline': deadline}
    exec(compile(ast.Module(body=[wait], type_ignores=[]), str(source), 'exec'), scope)
    return scope['wait']


@pytest.mark.parametrize('advance', [.12, .13])
def test_guard_crosses_deadline_without_sleep(advance):
    now = [0.]
    sleeps = []
    guards = []
    def guard():
        guards.append(True)
        now[0] += advance
    clock = SimpleNamespace(monotonic=lambda: now[0], sleep=sleeps.append)
    make_wait(clock, guard)(.12)
    assert sleeps == []
    assert len(guards) == 2  # Final safety check is still executed.


def test_normal_wait_only_sleeps_positive_remaining_time():
    now = [0.]
    sleeps = []
    def sleep(seconds):
        assert seconds > 0
        sleeps.append(seconds)
        now[0] += seconds
    clock = SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
    make_wait(clock, lambda: None)(.05)
    assert sum(sleeps) == pytest.approx(.05)
    assert max(sleeps) <= .02


def test_guard_stop_propagates_without_sleep():
    sleeps = []
    def guard():
        raise ValueError('选菜已停止，未开始制作')
    clock = SimpleNamespace(monotonic=lambda: 0., sleep=sleeps.append)
    with pytest.raises(ValueError, match='选菜已停止'):
        make_wait(clock, guard)(.12)
    assert sleeps == []


def test_wait_does_not_extend_original_selection_deadline():
    now = [0.]
    sleeps = []
    def sleep(seconds):
        assert seconds > 0
        sleeps.append(seconds)
        now[0] += seconds
    clock = SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
    make_wait(clock, lambda: None, deadline=.03)(.12)
    assert now[0] == pytest.approx(.03)
    assert sum(sleeps) == pytest.approx(.03)
