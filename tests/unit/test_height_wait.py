from unittest.mock import Mock
from utils.core.navigation_progress import HeightWaitBudget
from tests.unit.test_collection_continuity import game_and_nav, run


def test_height_wait_accumulates_across_interruptions_but_new_attempt_resets():
    budget=HeightWaitBudget(15.)
    budget.account((1,0,False),8.)
    # Menus, successful height samples and manual pauses do not account time.
    budget.account((1,0,False),7.)
    assert budget.exhausted
    budget.account((1,0,True),1.)
    assert not budget.exhausted and budget.elapsed==1.


def test_wrong_height_ends_attempt_without_collecting_or_arrival():
    game,nav=game_and_nav();report=Mock()
    assert not run(game,nav,[(0,3,0)],report,collect_wait_seconds=.1,continuous_recovery=True,recovery_attempts=3)
    assert game.time<40.
    assert any(c.kwargs.get('incident_kind')=='height_wait_timeout' for c in report.call_args_list)
    assert not any(c.args[0]=='reached' for c in report.call_args_list)
    assert not any(e.get('key')=='f' for _,e in game.events)
    assert not game.keys


def test_manual_pause_is_excluded_from_height_wait():
    game,nav=game_and_nav();report=Mock()
    game.pause_at,game.resume_at=5.,25.
    assert not run(game,nav,[(0,3,0)],report,continuous_recovery=True,recovery_attempts=3)
    timeouts=[c for c in report.call_args_list if c.kwargs.get('incident_kind')=='height_wait_timeout']
    assert timeouts and 15. <= timeouts[0].kwargs['height_wait_seconds'] < 15.2
    assert game.time>=35.
