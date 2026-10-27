"""Anti-stall regressions; fake clocks/devices only, never a live game."""
import pytest

from tests.unit.test_coordinate_navigation import scene
from tests.unit.test_coordinate_player import Game, manual_points
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait
from utils.core.coordinate_route import make_basis
from utils.flows.coordinate_player import _Follower, drive_points


@pytest.mark.parametrize('gap', [{'state': 'loading'},
                               {'state': 'loading', 'cache_context': None, 'terrain_revision': None}])
def test_transient_scene_gap_retains_same_scene_failed_approach(gap):
    snapshots = [dict(scene(), cache_context='a', terrain_revision=1)]
    nav = LocalNavigator(lambda: snapshots[0], water_areas=(), clock=lambda: 0.)
    nav.next_target((0, 0, 0), (0, 0, 6))
    nav.blocked((0, 0, 0), (0, 0, 3), attempt_id=1)
    snapshots[0] = gap
    with pytest.raises(NavigationWait):
        nav.next_target((0, 0, 0), (0, 0, 6))
    snapshots[0] = dict(scene(), cache_context='a', terrain_revision=1)
    nav.next_target((0, 0, 0), (0, 0, 6))
    assert nav.risks.count == 1


def test_moving_steering_target_cannot_hide_stationary_player():
    game = Game()
    game.stop_at = 15.
    state = {'calibrating': False}
    sleep = game.sleep

    def frozen(dt):
        game.speed = 3 if state['calibrating'] else 0
        sleep(dt)
    game.sleep = frozen

    class JitteringNavigator(LocalNavigator):
        calls = 0

        def next_target(self, position, target, **kwargs):
            point = super().next_target(position, target, **kwargs)
            self.calls += 1
            return (point[0]+(.001 if self.calls % 2 else -.001), point[1], point[2])

    nav = JitteringNavigator(lambda: scene(), water_areas=(), clock=game.monotonic)
    stalls = []

    def report(phase, message, **extra):
        state['calibrating'] = phase == 'calibrating'
        if extra.get('incident_kind') == 'movement_stall':
            stalls.append(game.time)
            game.stop_at = game.time

    drive_points(manual_points([(0, 0, 6)]), game.size(), lambda: game,
                 game, game, game, report, clock=game, recovery_attempts=2,
                 jump_on_recovery=False, collect_wait_seconds=0.,
                 continuous_recovery=True, navigator=nav)
    assert stalls and stalls[0] < 8.
    assert not game.keys and game.closed


def test_same_failed_episode_does_not_repeat_jump_on_every_recovery():
    game = Game(speed=0)
    phases = []
    player = _Follower({'duration': 20., 'client_size': game.size()}, game, game, game, game,
                       lambda s, m, **e: phases.append(e.get('recovery_phase')), game,
                       waypoints=[{'position': (0, 0, 6)}], recovery_attempts=5,
                       jump_on_recovery=True, continuous_recovery=True,
                       navigator=LocalNavigator(lambda: scene(), water_areas=(), clock=game.monotonic))
    player.recovery.begin((0, 0, 0))
    player.recovery.fail()
    player.recover((0, 0, 6), make_basis((0, 0, 1), (1, 0, 0)), (0, 0, 0), 0, 1)
    assert 'jump' not in phases
    assert 'detour' in phases


def test_new_scene_during_gap_discards_old_risk():
    snapshots = [dict(scene(), cache_context='a', terrain_revision=1)]
    nav = LocalNavigator(lambda: snapshots[0], water_areas=(), clock=lambda: 0.)
    nav.next_target((0, 0, 0), (0, 0, 6))
    nav.blocked((0, 0, 0), (0, 0, 3), attempt_id=1)
    snapshots[0] = {'state': 'loading', 'cache_context': 'b', 'terrain_revision': 1}
    with pytest.raises(NavigationWait):
        nav.next_target((0, 0, 0), (0, 0, 6))
    assert nav.risks.count == 0


def test_scene_gap_cannot_reuse_stale_path_and_risk_still_expires():
    snapshots, now = [dict(scene(), cache_context='a', terrain_revision=1)], [0.]
    nav = LocalNavigator(lambda: snapshots[0], water_areas=(), clock=lambda: now[0])
    nav.next_target((0, 0, 0), (0, 0, 6))
    nav.blocked((0, 0, 0), (0, 0, 3), attempt_id=1)
    snapshots[0] = None
    with pytest.raises(NavigationWait):
        nav.next_target((0, 0, 0), (0, 0, 6))
    assert not nav.path and nav.obstacles is None
    now[0] = 1000.
    with pytest.raises(NavigationWait):
        nav.next_target((0, 0, 0), (0, 0, 6))
    assert nav.risks.count == 0


def test_repeated_entry_is_temporary_directed_height_and_mode_specific():
    from utils.core.approach_risk import ApproachRisks
    now = [0.]
    risks = ApproachRisks(clock=lambda: now[0])
    risks.record((0, 0, 0), (0, 0, 3), attempt_id=1)
    assert not risks.repeats_failed_entry((0, 0), (0, 3), 0)
    risks.record((0, 0, 0), (0, 0, 3), attempt_id=1)
    assert not risks.repeats_failed_entry((0, 0), (0, 3), 0)
    risks.record((0, 0, 0), (0, 0, 3), attempt_id=2)
    assert risks.repeats_failed_entry((0, 0), (0, 3), 0)
    assert not risks.repeats_failed_entry((0, 3), (0, 0), 0)
    assert not risks.repeats_failed_entry((0, 0), (0, 3), 3)
    assert not risks.repeats_failed_entry((0, 0), (0, 3), 0, 'jump')
    assert not risks.repeats_failed_entry((0, 0), (2, 1), 0)
    now[0] = 1000.
    assert not risks.repeats_failed_entry((0, 0), (0, 3), 0)


def test_cached_repeated_entry_is_rejected_even_without_new_search():
    nav = LocalNavigator(lambda: scene(), water_areas=(), clock=lambda: 0.)
    target = (0, 0, 6)
    nav.next_target((0, 0, 0), target)
    for attempt in (1, 2):
        nav.blocked((0, 0, 0), (0, 0, 3), attempt_id=attempt)
    nav.next_target((0, 0, 0), target)
    # Simulate an old/cached straight corridor after all normal cache guards.
    nav.path = [(0, 0, 3), target]
    with pytest.raises(NavigationWait, match='重复失败方向'):
        nav.next_target((0, 0, 0), target)
    assert not nav.path


def test_position_watch_not_reset_by_rebinding_same_point_or_vertical_jump():
    from utils.core.navigation_progress import NavigationProgress
    watch = NavigationProgress()
    watch.bind((1, 0), (0, 0, 0), (0, 0, 6), 3.)
    watch.account(1.1)
    watch.bind((1, 0), (0, 0, 0), (1, 0, 6), 3.)
    watch.account(1.1)
    assert watch.stalled((0, 2, 0))
    watch.begin_probe((0, 0, 0))
    assert watch.elapsed == pytest.approx(2.2) and not watch.stalled((0, 0, 0))
    watch.account(2.1)
    assert not watch.stalled((1, 0, 0))
    watch.bind((1, 1), (1, 0, 0), (1, 0, 6), 3.)
    assert watch.elapsed == 0


def test_quantized_short_forward_pulse_cannot_reenter_repeated_failure():
    nav = LocalNavigator(lambda: scene(), water_areas=(), clock=lambda: 0.)
    nav.next_target((0, 0, 0), (0, 0, 6))
    for attempt in (1, 2):
        nav.blocked((0, 0, 0), (0, 0, 3), attempt_id=attempt)
    nav.next_target((0, 0, 0), (0, 0, 6))
    basis = make_basis((0, 0, 1), (1, 0, 0))
    assert nav.steering_keys((0, 0, 0), (0, 0, 6), basis, .1) != {'w'}
    assert nav.steering_keys((0, 0, 0), (0, 0, -6), basis, .1) == {'s'}


def test_guard_excludes_menus_planning_and_pause_from_point_budget():
    game = Game()
    nav = LocalNavigator(lambda: scene(), water_areas=(), clock=game.monotonic)
    player = _Follower({'duration': 1000., 'client_size': game.size()}, game, game, game, game,
                       lambda *a, **k: None, game, waypoints=[{'position': (0, 0, 6)}], navigator=nav)
    player.travel_progress.bind((1, 0), (0, 0, 0), (0, 0, 6), 3.)
    player.guard()
    game.time += 20  # Menu maintenance: not travelling.
    player.guard()
    player.travelling = player.navigation_waiting = True
    game.time += 20  # Planning or missing scene: no movement input.
    player.guard()
    player.navigation_waiting = False
    game.time += 1
    player.guard()
    assert player.travel_progress.elapsed == 1
    from utils.flows.coordinate_player import _Paused
    game.running = False
    game.time += 20
    with pytest.raises(_Paused):
        player.guard()
    assert player.travel_progress.elapsed == 1


def test_point_budget_skips_unreachable_point_and_continues_to_collect_next(monkeypatch):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_NAV_POINT_DRIVE_BASE_SECONDS', 2.)
    monkeypatch.setattr(thresholds, 'COORD_NAV_POINT_DRIVE_FACTOR', 0.)
    game = Game()
    game.stop_at = 25.
    state = {'calibrating': False, 'clear': False}
    sleep = game.sleep

    def frozen_first(dt):
        game.speed = 3 if state['calibrating'] or state['clear'] else 0
        sleep(dt)
    game.sleep = frozen_first
    nav = LocalNavigator(lambda: scene(), water_areas=(), clock=game.monotonic)
    skipped, reached = [], []

    def report(phase, message, **extra):
        state['calibrating'] = phase == 'calibrating'
        if phase == 'skipped':
            skipped.append((extra['waypoint'], message))
            state['clear'] = True
        if phase == 'reached':
            reached.append(extra['waypoint'])
    drive_points(manual_points([(0, 0, 6), (2, 0, 0)]), game.size(), lambda: game,
                 game, game, game, report, clock=game, recovery_attempts=5,
                 jump_on_recovery=True, collect_wait_seconds=0.,
                 continuous_recovery=True, navigator=nav)
    assert len(skipped) == 1 and skipped[0][0] == 1 and '预算' in skipped[0][1]
    assert reached == [2]
    assert sum(e['type'] == 'key_down' and e['key'] == 'f' for _, e in game.events) == 1
    assert not game.keys and game.closed
