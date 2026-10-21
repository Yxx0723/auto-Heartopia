"""Native monitor display contracts, with no Tk, clock or game access."""
import pytest

from utils.core.coordinate_monitor_view import MonitorView
from utils.core.thresholds import COORD_MONITOR_TRAIL_POINTS


def test_named_role_details_do_not_require_or_invent_a_unity_pointer_chain():
    from utils.core.coordinate_monitor_view import locator_details
    text, validation = locator_details({'locator': 'mono-local-player', 'paths': [
        {'label': 'Character → LocalPlayerComponent → Hierarchy._position', 'address': 0x123456}]})
    assert 'LocalPlayerComponent' in text
    assert '0x123456' in text
    assert 'UnityPlayer.dll' not in text
    assert validation == '本地角色对象校验通过'


def test_legacy_pointer_details_still_show_the_original_root():
    from utils.core.coordinate_monitor_view import locator_details
    text, validation = locator_details({'valid_paths': 2, 'configured_paths': 3, 'paths': [
        {'rva': 0x10, 'offsets': [0x20], 'root_address': 0x100010, 'address': 0x200020}]})
    assert 'UnityPlayer.dll + 0x10' in text
    assert '0x100010' in text
    assert validation == '2 / 3 条读数一致'


def test_computed_world_position_details_have_no_fake_xyz_address():
    from utils.core.coordinate_monitor_view import locator_details
    text, _ = locator_details({'locator': 'mono-local-player', 'paths': [
        {'label': 'Hierarchy → parent transforms → world XYZ', 'address': None}]})
    assert '根节点世界坐标' in text
    assert '0x' not in text


def sample(position=(1., 2., 3.), sequence=1, session='a', **extra):
    return {'state': 'connected', 'position': position, 'session': session,
            'sequence': sequence, 'sample_age': 0., 'paths': [], **extra}


def test_reference_delta_and_planar_distance_use_current_real_position():
    view = MonitorView()
    view.accept(sample(), 10.)
    view.set_reference()
    view.accept(sample((4., 8., 7.), sequence=2), 11.)
    assert view.delta == (3., 6., 4.)
    assert view.reference_distance == 5.
    assert view.speed == 5.


@pytest.mark.parametrize('state', ['waiting', 'error', 'disconnected'])
def test_lost_connection_clears_values_and_breaks_trail(state):
    view = MonitorView()
    view.accept(sample(), 10.)
    view.accept({'state': state, 'position': None}, 10.1)
    assert view.position is None
    assert view.speed is None
    assert view.delta is None
    with pytest.raises(ValueError):
        view.set_reference()


def test_expired_feed_and_invalid_coordinates_are_never_displayed_as_live():
    view = MonitorView()
    view.accept(sample(), 10.)
    view.expire(12.)
    assert view.position is None
    view.accept(sample((float('nan'), 2, 3)), 13.)
    assert view.position is None
    view.accept(sample(sample_age=10.), 14.)
    assert view.position is None


def test_pause_is_explicit_and_resume_does_not_connect_across_missing_movement():
    view = MonitorView()
    view.accept(sample(), 10.)
    view.set_paused(True)
    view.accept(sample((5., 2., 3.), sequence=2), 11.)
    view.expire(30.)
    assert view.position == (1., 2., 3.)
    with pytest.raises(ValueError):
        view.set_reference()
    view.set_paused(False)
    assert view.position is None
    view.accept(sample((5., 2., 3.), sequence=3), 31.)
    assert view.trail[0]['segment'] != view.trail[-1]['segment']


def test_new_process_session_clears_old_reference_and_trail():
    view = MonitorView()
    view.accept(sample(), 10.)
    view.set_reference()
    view.accept(sample((5., 2., 3.), session='b'), 10.2)
    assert view.reference is None
    assert len(view.trail) == 1


def test_slow_movement_accumulates_against_last_saved_point_and_duplicates_are_ignored():
    view = MonitorView()
    for i in range(20):
        view.accept(sample((1. + i * .005, 2., 3.), sequence=i), 10. + i * .1)
    assert len(view.trail) >= 4
    before = list(view.trail)
    view.accept(sample((2., 2., 3.), sequence=19), 12.)
    assert view.trail == before


def test_teleport_is_drawn_as_a_new_segment_instead_of_a_fake_walk():
    view = MonitorView()
    view.accept(sample(), 10.)
    view.accept(sample((500., 2., 3.), sequence=2), 10.1)
    assert view.trail[0]['segment'] != view.trail[-1]['segment']
    assert view.speed is None
    assert view.notice


def test_trail_is_bounded_and_clearing_it_does_not_modify_reference():
    view = MonitorView()
    view.accept(sample(), 0.)
    view.set_reference()
    for i in range(COORD_MONITOR_TRAIL_POINTS + 5):
        view.accept(sample((i * .1, 2., 3.), sequence=i + 2), i * .1 + .1)
    assert len(view.trail) == COORD_MONITOR_TRAIL_POINTS
    view.clear_trail()
    assert view.trail == []
    assert view.reference == (1., 2., 3.)


def test_direction_updates_on_turns_and_survives_standing_and_clearing_only_trail():
    view = MonitorView()
    view.accept(sample((0., 0., 0.)), 10.)
    assert view.heading is None
    view.accept(sample((1., 0., 0.), sequence=2), 10.1)
    assert view.heading == pytest.approx((1, 0))
    view.accept(sample((1., 0., 1.), sequence=3), 10.2)
    assert view.heading == pytest.approx((0, -1))
    view.accept(sample((1., 0., 1.), sequence=4), 10.3)
    view.clear_trail()
    assert view.heading == pytest.approx((0, -1))
    view.accept(sample((1., 0., 1.), sequence=5), 10.4)
    assert view.heading == pytest.approx((0, -1))


@pytest.mark.parametrize('reason', ['teleport', 'session', 'lost', 'gap', 'resume'])
def test_direction_never_carries_across_unknown_movement(reason):
    view = MonitorView()
    view.accept(sample((0., 0., 0.)), 10.)
    view.accept(sample((1., 0., 0.), sequence=2), 10.1)
    assert view.heading
    if reason == 'teleport': view.accept(sample((100., 0., 0.), sequence=3), 10.2)
    if reason == 'session': view.accept(sample((1., 0., 0.), sequence=3, session='b'), 10.2)
    if reason == 'lost': view.accept({'state': 'waiting'}, 10.2)
    if reason == 'gap': view.accept(sample((1., 0., 0.), sequence=3), 15.)
    if reason == 'resume':
        view.set_paused(True)
        assert view.heading
        view.set_paused(False)
    assert view.heading is None


def test_slow_movement_accumulates_but_small_stationary_jitter_does_not_turn_arrow():
    view = MonitorView()
    view.accept(sample((0., 0., 0.)), 10.)
    for i in range(1, 11):
        view.accept(sample((i*.01, 0., 0.), sequence=i+1), 10.+i*.1)
    assert view.heading == pytest.approx((1, 0))
    for i in range(11, 20):
        view.accept(sample((.1, 0., (-1)**i*.005), sequence=i+1), 10.+i*.1)
    assert view.heading == pytest.approx((1, 0))
