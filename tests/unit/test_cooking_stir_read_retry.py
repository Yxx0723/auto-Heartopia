"""按住 ALT 时读到未就绪：松开后整段重读，已备料/已派发则不重试。"""
import ast
import contextlib
from pathlib import Path

import pytest

from utils.core.map_targeting import MapLayoutUpdating
from utils.core.scene_read import SceneReadIssue
from utils.drivers.cooking_single_pot import CookingObservationUpdating

SOURCE = Path(__file__).resolve().parents[2] / 'utils/flows/cooking_single_pot.py'
NAMES = {'SceneReadIssue': SceneReadIssue, 'MapLayoutUpdating': MapLayoutUpdating,
         'CookingObservationUpdating': CookingObservationUpdating}


def make_click(read, click_body, presses):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    outer = next(node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'run_single_pot')
    click = next(node for node in outer.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'click')
    factory = (
        'def make(read, click_body, cooking_alt, device, window, guard, wait, report):\n'
        '    alt_interaction_active = False\n'
        '    pending_ack = None\n'
        '    ack_succeeded = False\n'
        + ''.join('    ' + line + '\n' for line in ast.unparse(click).splitlines()) +
        '    return click\n')

    @contextlib.contextmanager
    def alt(device, window, guard, wait, report):
        presses.append(True)
        yield

    namespace = dict(NAMES)
    exec(compile(factory, str(SOURCE), 'exec'), namespace)
    reports = []
    click = namespace['make'](read, click_body, alt,
                              type('D', (), {'buttons': set(), 'keys': set()})(),
                              object(), lambda: None, lambda seconds: None,
                              lambda state, message, **extra: reports.append(state))
    return click, reports


def test_read_not_ready_before_prepare_retries_the_whole_alt_sequence():
    calls = []
    presses = []
    errors = [SceneReadIssue('场景引用无效', kind='invalid_reference', stage='hud'),
              MapLayoutUpdating('界面变换数值正在更新')]

    def click_body(read_target, before_input=None, expected=None, *, dispatch_state=None):
        calls.append(True)
        if errors:
            raise errors.pop(0)
        return 'clicked'

    click, reports = make_click(lambda source=None: 'target', click_body, presses)
    assert click(lambda: 'target', None, hold_alt=True) == 'clicked'
    assert len(calls) == 3          # 两次未就绪 + 一次成功
    assert len(presses) == 3        # 每次都重新按下 ALT
    assert reports.count('stir_retry') == 2


def test_prepared_or_dispatched_is_never_replayed():
    presses = []

    def prepared(read_target, before_input=None, expected=None, *, dispatch_state=None):
        dispatch_state['prepared'] = True
        raise SceneReadIssue('场景引用无效', kind='invalid_reference', stage='hud')

    click, reports = make_click(lambda source=None: 'target', prepared, presses)
    with pytest.raises(SceneReadIssue):
        click(lambda: 'target', None, hold_alt=True)
    assert len(presses) == 1
    assert 'stir_retry' not in reports

    def sent(read_target, before_input=None, expected=None, *, dispatch_state=None):
        dispatch_state['sent'] = True
        raise SceneReadIssue('场景引用无效', kind='invalid_reference', stage='hud')

    presses.clear()
    click, reports = make_click(lambda source=None: 'target', sent, presses)
    with pytest.raises(SceneReadIssue):
        click(lambda: 'target', None, hold_alt=True)
    assert len(presses) == 1


def test_non_retryable_kind_and_three_attempt_limit():
    presses = []
    calls = []

    def not_retryable(read_target, before_input=None, expected=None, *, dispatch_state=None):
        calls.append(True)
        raise SceneReadIssue('场景对象缺少字段：x', kind='shape', stage='hud')

    click, reports = make_click(lambda source=None: 'target', not_retryable, presses)
    with pytest.raises(SceneReadIssue):
        click(lambda: 'target', None, hold_alt=True)
    assert len(calls) == 1 and len(presses) == 1

    calls.clear(); presses.clear()

    def always_unready(read_target, before_input=None, expected=None, *, dispatch_state=None):
        calls.append(True)
        raise MapLayoutUpdating('界面变换数值正在更新')

    click, reports = make_click(lambda source=None: 'target', always_unready, presses)
    with pytest.raises(MapLayoutUpdating):
        click(lambda: 'target', None, hold_alt=True)
    assert len(calls) == 3 and len(presses) == 3
    assert reports.count('stir_retry') == 2
