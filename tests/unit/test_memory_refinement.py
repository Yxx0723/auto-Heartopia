"""Recent reliable corridor reuse, without inventing travelled connections."""
import pytest
from tests.unit.test_route_path_memory import trace
from tests.unit.test_coordinate_player import manual_points
from utils.drivers.route_path_memory import RoutePathMemory


def memory(tmp_path, target=(0,0,8)):
    return RoutePathMemory(tmp_path/'route.json', manual_points([target]))


def test_small_jitter_merges_success_counts_without_averaging_geometry(tmp_path):
    m = memory(tmp_path)
    first = trace()
    jitter = [[.1 if i not in (0,8) else 0., 0., float(i)] for i in range(9)]
    m.remember(0, first, 4.)
    m.remember(0, jitter, 5.)
    assert len(m.legs['0']) == 1
    kept = m.legs['0'][0]
    assert kept in (first,jitter)
    assert m.statistics[m._identity(kept)]['successes'] == 2


def test_recent_typical_duration_overrides_one_lucky_fast_run(tmp_path):
    m = memory(tmp_path)
    m.remember(0, trace(), 1.)
    for _ in range(4):m.remember(0, trace(), 12.)
    alternative = [[0.,0.,0.],[4.,0.,0.],[4.,0.,4.],[4.,0.,8.],[0.,0.,8.]]
    m.remember(0, alternative, 6.)
    assert m.lookup(0,(0,0,0))[1] == (4.,0.,0.)


def test_same_geometry_can_use_measured_segment_times_for_remaining_cost(tmp_path):
    m = memory(tmp_path, (0,0,10))
    slow_tail = [[0.,0.,float(i)] for i in range(11)]
    alternative = [[0.,0.,float(i)] for i in range(6)] + [[2.,0.,5.],[2.,0.,8.],[0.,0.,10.]]
    m.remember(0, slow_tail, 10., sample_times=[i*.2 if i<=5 else 1+(i-5)*1.8 for i in range(11)])
    m.remember(0, alternative, 12., sample_times=[float(i)*11/5 if i<=5 else 11+(i-5)/3 for i in range(9)])
    assert m.lookup(0,(0,0,5))[-2] == (2.,0.,8.)


def test_near_return_is_not_erased_into_an_unwalked_connection(tmp_path):
    m = memory(tmp_path)
    path = [[0.,0.,0.],[1.,0.,0.],[2.,0.,0.],[2.,0.,1.],[1.,0.,1.],[.2,0.,0.],*[ [0.,0.,float(i)] for i in range(1,9)]]
    m.remember(0,path,8.)
    assert m.legs['0'][0] == path


def test_rejection_is_immediate_and_can_persist_without_another_success(tmp_path):
    m = memory(tmp_path)
    m.remember(0,trace(),4.)
    m.lookup(0,(0,0,0));m.reject_selected()
    before = m._cost(trace())
    m.flush_rejections()
    loaded=memory(tmp_path)
    assert loaded.statistics[loaded._identity(trace())]['blocked']==1
    assert loaded._cost(trace()) == pytest.approx(before)


def test_disconnected_height_variants_are_not_merged(tmp_path):
    m=memory(tmp_path)
    m.remember(0,trace(),4.)
    upper=[[0.,1.,float(i)] for i in range(8)]+[[0.,0.,8.]]
    m.remember(0,upper,4.)
    assert len(m.legs['0'])==2


def test_pending_failure_does_not_double_penalize_after_persistence(tmp_path):
    m=memory(tmp_path)
    m.remember(0,trace(),4.)
    m.lookup(0,(0,0,0));m.reject_selected()
    expected=m._cost(trace())
    m.flush_rejections()
    assert m._cost(trace())==pytest.approx(expected)


def test_success_window_is_bounded_and_reliability_recovers(tmp_path):
    m=memory(tmp_path)
    m.remember(0,trace(),4.)
    m.lookup(0,(0,0,0));m.reject_selected();m.flush_rejections()
    penalized=m._cost(trace())
    for _ in range(12):m.remember(0,trace(),4.)
    record=m.statistics[m._identity(trace())]
    assert len(record['recent_seconds'])==7 and len(record['recent_outcomes'])==7
    assert m._cost(trace())<penalized
    assert record['loot_confirmed'] is False


def test_invalid_segment_profile_falls_back_without_losing_geometry(tmp_path):
    m=memory(tmp_path)
    assert m.remember(0,trace(),4.,sample_times=[float('nan')]*9)
    assert m.lookup(0,(0,0,0))
    assert 'sample_times' not in m.statistics[m._identity(trace())]


def test_connector_check_can_reject_a_near_but_blocked_entry(tmp_path):
    m=memory(tmp_path)
    m.remember(0,trace(),4.)
    assert not m.lookup(0,(.5,0,0),can_connect=lambda point:False)
    assert m.selected is None


def test_atomic_write_failure_does_not_invent_success_or_forget_pending_failure(tmp_path,monkeypatch):
    m=memory(tmp_path)
    m.remember(0,trace(),4.)
    m.lookup(0,(0,0,0));m.reject_selected()
    def fail(*a):raise OSError('disk unavailable')
    monkeypatch.setattr('utils.drivers.route_path_memory.atomic_write_json',fail)
    with pytest.raises(OSError):m.remember(0,trace(),2.)
    assert m.statistics[m._identity(trace())]['successes']==1
    assert m.rejected and not m._rejections_applied


def test_navigation_records_real_active_time_profile_and_unconfirmed_collection(tmp_path):
    from tests.unit.test_route_path_memory import run_walk
    from tests.unit.test_coordinate_navigation import NavigationGame, scene
    data=manual_points([(0,0,8)])
    m=memory(tmp_path)
    assert run_walk(NavigationGame(),data,m,scene())[0]
    record=next(iter(m.statistics.values()))
    assert len(record['sample_times'])==len(m.legs['0'][0])
    assert record['sample_times'][0]==0
    assert record['sample_times'][-1]==pytest.approx(record['profile_duration'])
    assert record['collection_outcome']=='action_completed_unconfirmed_loot'
    assert record['loot_confirmed'] is False


def test_jump_in_a_learning_leg_does_not_create_walk_evidence(tmp_path):
    from tests.unit.test_coordinate_navigation import scene
    from utils.core.coordinate_navigation import LocalNavigator
    m=memory(tmp_path)
    nav=LocalNavigator(lambda:scene(),path_memory=m,water_areas=())
    nav.set_waypoint(0)
    nav.next_target((0,0,0),(0,0,8))
    nav.discard_learning()
    nav.arrived((0,0,8))
    assert m.count==0 and not m.path.exists()


def test_storage_budget_rejection_does_not_publish_unwritten_success(tmp_path):
    m=memory(tmp_path)
    assert m.remember(0,trace(),4.)
    m.lookup(0,(0,0,0));m.reject_selected()
    m.max_total_points=1
    assert not m.remember(0,trace(),2.)
    assert m.statistics[m._identity(trace())]['successes']==1
    assert m.rejected and not m._rejections_applied
