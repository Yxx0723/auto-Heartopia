import pytest

from utils.core.tool_durability import axe_pick_status
from utils.core.tool_repair import ToolRepairCycle


def tool(current, held=True):
    return axe_pick_status(current, 1100, True, held)


def test_threshold_is_strictly_below_twenty_percent_and_requires_held_tool():
    cycle = ToolRepairCycle()
    assert cycle.observe(tool(220), 0.) == 'idle'
    assert cycle.observe(tool(219), 0.) == 'needed'
    assert cycle.observe(tool(0), 0.) == 'needed'
    assert cycle.observe(tool(100, False), 0.) == 'not_held'


def test_one_use_waits_all_seventeen_seconds_even_if_threshold_has_already_recovered():
    cycle = ToolRepairCycle()
    cycle.mark_used(tool(110), 10.)
    for at in (10., 11., 20., 26.999):
        assert cycle.observe(tool(670), at) == 'waiting'
    assert cycle.observe(tool(670), 27.) == 'completed'
    assert not cycle.active
    assert cycle.observe(tool(670), 28.) == 'idle'


def test_pause_or_delayed_resume_does_not_restart_real_time_effect_or_consume_twice():
    cycle = ToolRepairCycle()
    cycle.mark_used(tool(110), 0.)
    with pytest.raises(ValueError):
        cycle.mark_used(tool(110), 2.)
    assert cycle.observe(tool(670), 40.) == 'completed'


@pytest.mark.parametrize('current', [0, 110, 219])
def test_no_effect_or_insufficient_recovery_retains_attempt_to_prevent_repeated_consumption(current):
    cycle = ToolRepairCycle()
    cycle.mark_used(tool(110), 0.)
    assert cycle.observe(tool(current), 17.) == 'failed'
    assert cycle.active
    with pytest.raises(ValueError):
        cycle.mark_used(tool(current), 30.)


def test_missing_data_is_unknown_and_can_be_verified_later_without_second_use():
    cycle = ToolRepairCycle()
    assert cycle.observe({'state': 'unavailable'}, 0.) == 'unavailable'
    cycle.mark_used(tool(110), 0.)
    assert cycle.observe({'state': 'unavailable'}, 10.) == 'waiting'
    assert cycle.observe({'state': 'unavailable'}, 17.) == 'unavailable'
    assert cycle.active
    assert cycle.observe(tool(670), 18.) == 'completed'


def test_different_tool_or_maximum_is_not_claimed_as_success():
    cycle = ToolRepairCycle()
    cycle.mark_used(tool(110), 0.)
    assert cycle.observe(tool(670, False), 17.) == 'not_held'
    assert cycle.observe(axe_pick_status(670, 1200, True, True), 17.) == 'changed'
    assert cycle.active
