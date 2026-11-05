"""A sent repair click is not evidence that the repair effect started."""
from unittest.mock import Mock

import pytest
from PIL import Image

from utils.core.tool_repair import ToolRepairCycle
from utils.core.tool_durability import axe_pick_status
from utils.vision.repair_box import RepairBoxMenu
from tests.unit.test_repair_box import TEMPLATE, frame
from tests.unit.test_coordinate_auto_repair import RepairGame, run


def tool(current):
    return axe_pick_status(current,1100,True,True)


def test_unobserved_effect_is_bounded_and_not_claimed_as_repairing():
    cycle=ToolRepairCycle();cycle.mark_used(tool(110),0.)
    assert not cycle.effect_confirmed
    assert cycle.observe(tool(110),5.)=='unconfirmed'
    assert cycle.active  # A late result must be checked before another use.
    assert cycle.observe(tool(671),18.)=='completed'


def test_real_durability_increase_confirms_effect_but_keeps_full_effect_window():
    cycle=ToolRepairCycle();cycle.mark_used(tool(110),0.)
    assert cycle.observe(tool(143),1.)=='waiting'
    assert cycle.effect_confirmed
    assert cycle.observe(tool(440),10.)=='waiting'
    assert cycle.observe(tool(671),17.)=='completed'


def test_closed_menu_after_preparation_never_produces_a_consuming_click():
    menu=RepairBoxMenu(TEMPLATE);window=Mock()
    window.pointer.return_value=(675,528,True)
    window.snapshot.return_value=Image.new('RGB',(1024,768))
    device=Mock()
    with pytest.raises(ValueError,match='面板|图标'):
        menu.confirm_target(window,{'x':675,'y':528},device,Mock(),Mock())
    assert all(c.args[0]['type']!='mouse_down' for c in device.execute.call_args_list)


def test_pointer_must_arrive_before_a_repair_target_is_accepted():
    menu=RepairBoxMenu(TEMPLATE);window=Mock();window.snapshot.return_value=frame()
    window.pointer.return_value=(500,400,True)
    with pytest.raises(ValueError,match='鼠标'):
        menu.confirm_target(window,{'x':675,'y':528},Mock(),Mock(),Mock())


@pytest.mark.parametrize('visible',[True,False])
def test_fresh_target_is_checked_after_pointer_settles(visible):
    menu=RepairBoxMenu(TEMPLATE);window=Mock();window.snapshot.return_value=frame()
    window.pointer.return_value=(675,528,visible);device=Mock();wait=Mock()
    target=menu.confirm_target(window,{'x':675,'y':528},device,Mock(),wait)
    assert target['x']==675 and target['y']==528
    assert device.execute.call_args.args[0]['type']=='cursor_move'
    assert wait.called


def test_no_effect_reports_verification_instead_of_a_fake_recovery_countdown():
    game=RepairGame(effect=False);game.stop_at=8.
    result,statuses=run(game)
    assert not result and len(game.use_times)==1
    assert any(s=='repairing' and x.get('repair_phase')=='verifying' for _,s,_,x in statuses)
    assert not any(s=='repairing' and x.get('repair_phase')=='waiting' for _,s,_,x in statuses)
    pauses=[(at,x) for at,s,_,x in statuses if s=='repair_paused']
    assert pauses and pauses[0][0]<7.
    assert pauses[0][1]['repair_current']==110
    assert pauses[0][1]['repair_before']==110


def test_target_recheck_failure_never_marks_or_uses_the_box():
    from tests.unit.test_coordinate_auto_repair import FakeBox
    game=RepairGame();game.stop_at=1.;box=FakeBox(game)
    box.confirm_target=Mock(side_effect=ValueError('T 面板未确认'))
    result,statuses=run(game,box=box)
    assert not result and not game.use_times
    assert any('T 面板未确认' in text for _,_,text,_ in statuses)
