"""Regression: delayed resource updates between F presses must not be lost."""
from unittest.mock import Mock

import pytest

from tests.unit.test_special_collection import follower


def bag(count, scope='town-player-bag'):
    return {'scope': scope, 'counts': {40026: count, 40006: 123}}


def test_delayed_drop_during_maintenance_uses_first_resource_and_bag_baseline():
    f, game, point, target, _ = follower()
    game.backpack_counts_snapshot = Mock(return_value=bag(83))
    target['resource_available'] = 3
    assert not f.collect_point(point, 0, 1)
    game.sleep(17)  # Repair between presses; the old per-press window is over.
    target['resource_available'] = 2
    game.backpack_counts_snapshot.return_value = bag(84)
    assert f.collect_point(point, 0, 1)
    result = f._report.call_args.kwargs
    assert result['resource_confirmed'] is True
    assert result['resource_inventory_delta'] == 1
    assert result['resource_available_before'] == 3
    assert sum(e.get('type') == 'key_down' and e.get('key') == 'f' for _, e in game.events) == 1


def test_counter_drop_without_matching_inventory_gain_is_not_confirmed():
    f, game, point, target, _ = follower()
    game.backpack_counts_snapshot = Mock(return_value=bag(83))
    game.on_input = lambda event: target.update(resource_available=1)
    assert f.collect_point(point, 0, 1)
    assert f._report.call_args.kwargs['resource_confirmed'] is False


def test_delayed_bag_gain_after_resource_disappears_is_confirmed():
    f, game, point, _, nav = follower()
    game.backpack_counts_snapshot = lambda: bag(84 if game.monotonic() > 3 else 83)
    game.on_input = lambda event: nav.scene_provider.return_value.update(objects=[])
    assert f.collect_point(point, 0, 1)
    assert f._report.call_args.kwargs['resource_inventory_delta'] == 1
    assert not game.keys


@pytest.mark.parametrize('fault', ['scope', 'unreadable', 'other_item'])
def test_foreign_or_unreadable_inventory_never_confirms_collection(fault):
    f, game, point, target, _ = follower()
    game.backpack_counts_snapshot = Mock(return_value=bag(83))
    assert not f.collect_point(point, 0, 1)
    target['resource_available'] = 1
    if fault == 'scope':
        game.backpack_counts_snapshot.return_value = bag(84, 'other-town')
    elif fault == 'unreadable':
        game.backpack_counts_snapshot.side_effect = ValueError('unstable')
    else:
        game.backpack_counts_snapshot.return_value = {'scope': 'town-player-bag', 'counts': {40026: 83, 40006: 124}}
    assert f.collect_point(point, 0, 1)
    assert f._report.call_args.kwargs['resource_confirmed'] is False


def test_missing_pre_press_inventory_is_never_rebased_after_input():
    f, game, point, target, _ = follower()
    game.backpack_counts_snapshot = Mock(side_effect=ValueError('unstable'))
    assert not f.collect_point(point, 0, 1)
    game.backpack_counts_snapshot.side_effect = None
    game.backpack_counts_snapshot.return_value = bag(100)
    target['resource_available'] = 1
    assert f.collect_point(point, 0, 1)
    assert f._report.call_args.kwargs['resource_confirmed'] is False


def test_whole_process_evidence_survives_pause_during_press():
    from utils.flows.coordinate_player import _Paused
    f, game, point, target, _ = follower()
    game.backpack_counts_snapshot = Mock(return_value=bag(83))
    game.pause_at = .01
    with pytest.raises(_Paused):
        f.collect_point(point, 0, 1)
    evidence = f.special_progress['evidence']
    assert not game.keys
    game.pause_at = float("inf")
    game.backpack_counts_snapshot.return_value = bag(84)
    target['resource_available'] = 1
    assert f.collect_point(point, 0, 1)
    assert f.special_progress['evidence'] is evidence
    assert f._report.call_args.kwargs['resource_inventory_delta'] == 1


def test_recycled_object_cannot_confirm_old_attempt_even_with_bag_gain():
    from utils.core.harvest_evidence import HarvestEvidence
    target = {'resource_available': 3, 'resource_entity': 100, 'resource_component': 200}
    evidence = HarvestEvidence('fluorite', target, bag(83))
    evidence.pressed = True
    evidence.observe(dict(target, resource_entity=101, resource_available=2), bag(84))
    assert evidence.fields()['resource_evidence_invalid'] is True
    assert evidence.fields()['resource_confirmed'] is False


def test_confirmed_gain_is_preserved_if_a_later_stock_sample_goes_down():
    from utils.core.harvest_evidence import HarvestEvidence
    evidence = HarvestEvidence('fluorite', {'resource_available': 3}, bag(83))
    evidence.pressed = True
    evidence.observe('unknown', bag(84))
    evidence.observe('unknown', bag(83))
    fields = evidence.fields()
    assert fields['resource_confirmed'] is True
    assert fields['resource_inventory_after'] == 84
    assert fields['resource_inventory_delta'] == 1
    assert fields['resource_inventory_last'] == 83


def test_stop_during_first_press_still_logs_the_initial_inventory_baseline():
    from utils.flows.coordinate_player import _Stopped
    f, game, point, _, _ = follower()
    game.backpack_counts_snapshot = lambda: bag(83)
    game.stop_at = .01
    with pytest.raises(_Stopped):
        f.collect_point(point, 0, 1)
    assert not game.keys
    assert f._report.call_args.kwargs['resource_inventory_before'] == 83
    assert f._report.call_args.kwargs['resource_confirmed'] is False


def test_history_keeps_one_result_for_repeated_confirmation_events(tmp_path):
    from utils.drivers.playback_history import PlaybackHistory
    history = PlaybackHistory(tmp_path, {}, 'harvest', {})
    for _ in range(3):
        history.event({'state': 'collecting', 'resource_evidence_id': 'same-target',
                       'resource_inventory_delta': 1, 'resource_confirmed': True})
    assert len(history.summary['harvest_evidence']) == 1
    history.close()


def test_late_inventory_gain_during_house_patrol_confirms_without_repressing_f():
    from utils.core.harvest_evidence import HarvestEvidence
    from tests.unit.test_house_patrol import run, scene, target
    from tests.unit.test_coordinate_player import Game
    s, game = scene(), Game()
    obj = target(position=[0., 0., 0.])
    s['objects'] = [obj]
    game.backpack_counts_snapshot = lambda: {'scope': 'same-bag', 'counts': {40006: int(game.time >= 5)}}
    attempts = []

    def runner(data, size, rf, device, window, controls, report, **options):
        point = data['points'][0]
        if 'special_resource' in point:
            attempts.append(1)
            evidence = HarvestEvidence('roaming_oak', obj, game.backpack_counts_snapshot())
            evidence.pressed = True
            options['harvest_journal'].append((game.time, evidence))
            s['objects'] = []
            report('collecting', 'counter only', resource_outcome='gathered', resource_confirmed=False)
        else:
            game.sleep(5)
            game.position = point['position']
            reader = rf()
            try:
                reader.read()
            finally:
                reader.close()
        return True

    result, events, _, _ = run(s, runner, game)
    assert result
    assert len(attempts) == 1
    assert any(e[2].get('patrol_result') == 'late_inventory_confirmed' for e in events)
    assert events[-1][2]['patrol_completed_resources'] == ['roaming_oak']
