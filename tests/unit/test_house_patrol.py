"""House patrol is an observation tour, never an implicit harvest route."""
from copy import deepcopy
from unittest.mock import Mock
import pytest

from tests.unit.test_coordinate_player import Game
from utils.core.special_patrol import make_patrol_data, patrol_target
from utils.flows.special_patrol import drive_special_patrol
from utils.core import special_verdict


STAMP = '2026-10-24T01:00:00+08:00'


def scene():
    return {'state': 'ready', 'house_entrance_state': 'ready',
            'patrol_context': [1, 2, 3, 4], 'objects': [], 'colliders': [],
            'house_entrances': [{'house_number': n, 'position': [n*10., 0., 0.],
                                 'source': 'main-map-house-player-position'} for n in range(1, 13)]}


def target(**extra):
    return {'category': 'roaming_oak', 'resource_static_id': 42001, 'map_dynamic': True,
            'position': [2., 0., 0.], 'resource_available': 2, **extra}


def data(s):
    return make_patrol_data(s, [100., 0., 0.], 'roaming_oak', '橡木', 3, STAMP)


def run(s, runner=None, game=None):
    game = game or Game()
    nav = Mock(scene_provider=lambda: s, water_areas=())
    events, legs = [], []
    def default_runner(d, size, rf, device, window, controls, report, **kw):
        legs.append(deepcopy(d)); r = rf()
        try:
            r.read()
            game.position = list(d['points'][0]['position']); r.read()
            return True
        finally:
            r.close(); device.release_all()
    result = drive_special_patrol(data(s), game.size(), lambda: game, game, game, game,
                lambda state, text, **kw: events.append((state, text, kw)), clock=game,
                navigator=nav, point_runner=runner or default_runner)
    return result, events, legs, game


def test_no_loaded_target_builds_house_plan_in_numeric_order_not_nearest():
    d = data(scene())
    assert d['special_patrol']['checkpoint_houses'] == list(range(1, 13))
    assert d['special_patrol']['checkpoints'][0] == [10., 0., 0.]


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'source', 'context'])
def test_incomplete_or_unscoped_house_data_never_invents_coordinates(fault):
    s = scene()
    if fault == 'missing': s['house_entrances'].pop()
    if fault == 'duplicate': s['house_entrances'][-1] = s['house_entrances'][0]
    if fault == 'source': s['house_entrances'][0]['source'] = 'guess'
    if fault == 'context': s['patrol_context'] = None
    with pytest.raises(ValueError): data(s)


@pytest.mark.parametrize('obj', [target(map_cached=True), target(map_dynamic=False),
                                  target(resource_static_id=42002), target(resource_available=0)])
def test_stale_wrong_or_exhausted_objects_never_become_live_targets(obj):
    s = scene(); s['objects'] = [obj]
    assert patrol_target(s, 'roaming_oak')[0] != 'live'


def test_visit_all_twelve_without_pressing_f_and_report_not_found():
    result, events, legs, game = run(scene())
    assert result
    assert [leg['points'][0]['patrol_house'] for leg in legs] == list(range(1, 13))
    assert all('special_resource' not in leg['points'][0] for leg in legs)
    assert not game.events
    assert events[-1][2]['patrol_result'] == 'not_found'
    assert events[-1][2]['patrol_checked'] == 12


def test_live_discovery_interrupts_walking_then_collects_and_confirms():
    s = scene(); game = Game(); seen = []
    def runner(d, size, rf, device, window, controls, report, **kw):
        p = d['points'][0]; seen.append(deepcopy(p)); r = rf()
        try:
            r.read()
            if 'special_resource' in p:
                report('collecting', 'ok', resource_outcome=special_verdict.GATHERED, resource_confirmed=True)
                return True
            s['objects'] = [target()]
            game.position = [4., 0., 0.]; r.read()
            pytest.fail('discovery should interrupt this leg')
        finally: r.close(); device.release_all()
    result, events, _, game = run(s, runner, game)
    assert result and len(seen) == 2 and 'special_resource' in seen[-1]
    assert events[-1][2]['patrol_result'] == 'gathered'
    assert events[-1][2]['patrol_checked'] == 0  # Interrupted house was not visited.


def test_unreachable_houses_receive_one_bounded_retry_then_partial():
    calls = []
    def runner(d, *args, **kw): calls.append(d); return False
    result, events, _, _ = run(scene(), runner)
    assert not result and len(calls) == 24
    assert events[-1][2]['patrol_missing'] == list(range(1, 13))


def test_missing_target_after_press_is_not_a_confirmed_drop():
    assert not special_verdict.confirmed(special_verdict.verdict({'available': 2}, None))


def test_scene_switch_aborts_before_more_navigation_or_collection():
    s = scene()
    def runner(d, size, rf, *args, **kw):
        r = rf()
        try:
            s['patrol_context'] = [9, 8, 7, 6]; r.read()
        finally: r.close()
    with pytest.raises(ValueError, match='切换'): run(s, runner)


def test_f9_during_scan_releases_and_stops_promptly():
    game = Game(); game.stop_at = .2
    result, _, _, _ = run(scene(), game=game)
    assert not result and game.time <= .22 and not game.keys


def test_missing_scene_has_bounded_wait_not_infinite_navigation():
    s = scene()
    def runner(d, *args, **kw):
        s['state'] = 'loading'
        return False
    result, events, _, game = run(s, runner)
    assert not result and 30. <= game.time < 32.
    assert events[-1][2]['patrol_result'] == 'data_unavailable'
    assert not game.keys


def test_ambiguous_live_objects_are_waited_on_not_collected():
    s = scene(); s['objects'] = [target(), target(position=[5.,0.,0.])]
    runner = Mock(side_effect=AssertionError('ambiguous target must never receive F'))
    result, events, _, _ = run(s, runner)
    assert not result and events[-1][2]['patrol_result'] == 'data_unavailable'
    runner.assert_not_called()


def test_paused_scan_does_not_skip_house_or_consume_dwell_budget():
    game = Game(); game.pause_at = .1; game.resume_at = 1.1
    result, events, _, game = run(scene(), game=game)
    assert result and game.time >= 37.
    assert events[-1][2]['patrol_checked'] == 12


def test_missing_target_and_cooling_remain_unconfirmed_after_collection():
    for outcome in (special_verdict.GONE, special_verdict.COOLING, special_verdict.UNKNOWN):
        s = scene(); s['objects'] = [target()]
        def runner(d, size, rf, device, window, controls, report, **kw):
            r = rf()
            try:
                r.read(); game = r.reader
                if 'special_resource' in d['points'][0]:
                    report('collecting', 'unconfirmed', resource_outcome=outcome)
                else:
                    game.position = d['points'][0]['position']; r.read()
                return True
            finally: r.close(); device.release_all()
        result, events, _, _ = run(s, runner)
        assert not result and events[-1][2]['patrol_result'] == 'unconfirmed'
        assert events[-1][2]['resource_confirmed'] is False


def test_actual_navigation_visits_houses_and_never_collects_at_door():
    from utils.core.coordinate_navigation import LocalNavigator
    s = scene(); game = Game(speed=6)
    for n, row in enumerate(s['house_entrances']): row['position'] = [(n+1)*4., 0., 0.]
    events = []
    result = drive_special_patrol(data(s), game.size(), lambda: game, game, game, game,
        lambda state, text, **kw: events.append((state,kw)), clock=game,
        navigator=LocalNavigator(lambda: s, clock=game.monotonic, water_areas=()))
    assert result and events[-1][1]['patrol_checked'] == 12
    assert not any(event.get('key') == 'f' for _, event in game.events)
    assert not game.keys


def test_actual_gather_with_counter_drop_and_zero_remaining_is_confirmed():
    from utils.core.coordinate_navigation import LocalNavigator
    s = scene(); obj = target(position=[0.,0.,0.]); s['objects'] = [obj]
    game = Game()
    game.backpack_counts_snapshot = lambda: {'scope':'local-bag', 'counts':{40006: 1 if any(e.get('key')=='f' for _,e in game.events) else 0}}
    def on_input(event):
        if event == {'type':'key_down','key':'f'}: obj['resource_available'] = 0
    game.on_input = on_input
    events = []
    result = drive_special_patrol(data(s), game.size(), lambda: game, game, game, game,
        lambda state, text, **kw: events.append((state,kw)), clock=game,
        navigator=LocalNavigator(lambda: s, clock=game.monotonic, water_areas=()))
    assert result and events[-1][1]['patrol_result'] == 'gathered'
    assert sum(e == {'type':'key_down','key':'f'} for _,e in game.events) == 1
    assert not game.keys


def test_real_door_wall_is_not_ignored_by_the_patrol():
    s = scene(); s['colliders'] = [{'polygon':[[-100,-100],[200,-100],[200,100],[-100,100]],'height':[0,10]}]
    runner = Mock(side_effect=AssertionError('solid door area cannot be navigated to'))
    result, events, _, _ = run(s, runner)
    assert not result and len(events[-1][2]['patrol_failed']) == 12
    runner.assert_not_called()


def test_replacement_entity_counter_cannot_prove_previous_target_was_collected():
    before = {'available': 2, 'entity': 100, 'component': 200}
    assert special_verdict.verdict(before, {'available': 1, 'entity': 101, 'component': 200}) == 'unknown'


def test_worker_dispatches_patrol_without_changing_saved_route_or_enabling_memory(monkeypatch):
    from utils.ui.point_playback import PointPlayback
    from utils.flows.coordinate_player import drive_points
    from tests.unit.test_point_playback import FakeWindow, WaitingControls
    calls = []
    def patrol(d, *args, **kwargs):
        calls.append((deepcopy(d), kwargs))
        args[5]('patrol_finished', 'done', patrol_result='not_found')
        return True
    monkeypatch.setattr('utils.flows.special_patrol.drive_special_patrol', patrol)
    runner = drive_points
    d = data(scene()); original = deepcopy(d)
    worker = PointPlayback('profile.json', runner=runner, window_factory=FakeWindow,
        controls_factory=WaitingControls, device_factory=lambda window: Mock(), scene_provider=lambda pid: scene())
    worker.start(d, 77, auto_navigate=True, remember_paths=False, auto_eat=True, food_fallback='fruit_mushroom')
    worker.thread.join(timeout=2)
    assert worker.take_latest()['state'] == 'finished'
    assert calls and calls[0][1]['point_runner'] is runner
    assert calls[0][1]['food_fallback'] == 'fruit_mushroom'
    assert worker.path_memory is None and d == original
    worker.close()


def test_food_wait_is_not_navigation_timeout_even_while_coordinates_are_read():
    game = Game(); first = True
    def runner(d, size, rf, device, window, controls, report, **kw):
        nonlocal first
        reader = rf()
        try:
            if first:
                first = False
                report('eating', 'waiting for food confirmation')
                for _ in range(350):
                    controls.snapshot(); reader.read(); game.sleep(.1)
            report('playing', 'food complete, resuming')
            game.position = d['points'][0]['position']; reader.read()
            return True
        finally: reader.close(); device.release_all()
    result, events, _, _ = run(scene(), runner, game)
    assert result and events[-1][2]['patrol_checked'] == 12
    assert not any(row[2].get('patrol_result') == 'unreachable' for row in events)


def test_changed_unconfirmed_candidates_have_a_bounded_total_attempt_budget():
    s = scene(); obj = target(); s['objects'] = [obj]; attempts = []
    def runner(d, size, rf, device, window, controls, report, **kw):
        reader = rf()
        try:
            if 'special_resource' in d['points'][0]:
                attempts.append(1)
                report('collecting', 'unknown', resource_outcome='unknown')
            else:
                obj['position'] = [obj['position'][0]+5., 0., 0.]
                game = reader.reader; game.position = d['points'][0]['position']; reader.read()
            return True
        finally: reader.close(); device.release_all()
    result, events, _, _ = run(s, runner)
    assert not result and len(attempts) == 3
    assert events[-1][2]['patrol_result'] == 'unconfirmed'
