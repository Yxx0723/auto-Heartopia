"""菜单快照遇到瞬时读取问题时应整份重读，而不是当成 READ_FAILED 停批。"""
from types import SimpleNamespace

import pytest

from utils.core.map_targeting import MapLayoutUpdating
from utils.core.scene_read import SceneReadIssue
from utils.drivers.cooking_menu_state import CookingMenuStateChanged, CookingMenuStateReader


def make_reader(ui_error=None, context_error=None):
    reader = object.__new__(CookingMenuStateReader)
    reader.memory = SimpleNamespace(locator=SimpleNamespace(snapshot=lambda: None))

    def raise_if(error):
        if error is not None:
            raise error

    def ui_views():
        raise_if(ui_error)
        return (1, 2, 3, (10,))

    def context():
        raise_if(context_error)
        return (1, 2, 3)

    reader.ui = SimpleNamespace(ui_views=ui_views)
    reader.recipe = SimpleNamespace(context=context)
    return reader


@pytest.mark.parametrize('error', [
    SceneReadIssue('场景引用无效', kind='invalid_reference'),
    SceneReadIssue('场景数据正在变化', kind='mutation'),
    MapLayoutUpdating('界面变换数值正在更新'),
])
def test_transient_read_errors_are_rethrown_for_the_outer_retry(error):
    reader = make_reader(ui_error=error)
    with pytest.raises((SceneReadIssue, MapLayoutUpdating)) as caught:
        reader.snapshot()
    assert caught.value is error


def test_non_retryable_source_errors_still_read_failed():
    reader = make_reader(ui_error=OSError('UI read'), context_error=RuntimeError('context read'))
    with pytest.raises(CookingMenuStateChanged) as caught:
        reader.snapshot()
    assert caught.value.classification == 'READ_FAILED'
    assert not caught.value.recoverable
