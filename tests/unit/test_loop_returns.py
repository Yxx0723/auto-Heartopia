"""Consecutive samples in one region are not independent return events."""
from utils.core.navigation_progress import NavigationProgress


def decisions(points):
    p=NavigationProgress();p.bind('point',points[0],(0,0,0),3.)
    result=[]
    for pos in points:
        p.account(.5);result.append(p.looping(pos))
    return p,result


def test_long_approach_then_small_final_adjustments_are_not_four_returns():
    approach=[(0,0,12-i*.5) for i in range(23)]
    adjustments=[(.48,0,.05),(.46,0,.04),(.44,0,.03),(.42,0,.02),(.4,0,.01)]
    _,result=decisions(approach+adjustments)
    assert not any(result)


def test_multiple_consecutive_samples_do_not_inflate_one_return_event():
    points=[(0,0,0),(0,0,2),(2,0,2),(2,0,0)]*3+[(0,0,.05)]*18
    _,result=decisions(points)
    assert not any(result)


def test_jitter_at_the_return_radius_is_not_an_exit_and_new_entry():
    approach=[(0,0,12-i*.5) for i in range(23)]
    _,result=decisions(approach+[(0,0,.7),(0,0,.8)]*8)
    assert not any(result)


def test_real_repeated_departures_and_returns_still_trigger_the_guard():
    _,result=decisions([(0,0,0),(2,0,0),(2,0,2),(0,0,2)]*8)
    assert any(result)


def test_stationary_jitter_still_has_a_finite_unchanged_point_budget():
    p,_=decisions([(0,0,.4),(0,0,.5)]*16)
    budget=p.budget;p.bind('point',(0,0,.5),(0,0,0),3.)
    assert p.budget==budget and p.elapsed==16.
    p.account(budget)
    assert p.exhausted


def test_new_point_clears_return_history_but_replanning_the_same_point_does_not():
    p,result=decisions([(0,0,0),(2,0,0),(2,0,2),(0,0,2)]*8)
    assert any(result)
    p.bind('point',(0,0,0),(0,0,0),3.)
    assert p.visits
    p.bind('other',(0,0,0),(0,0,0),3.)
    assert not p.visits and p.elapsed==0.
