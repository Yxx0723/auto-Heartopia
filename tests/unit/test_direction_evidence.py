"""Synthetic observations, not game-verified movement abilities."""
import pytest
from utils.core.direction_evidence import DirectionEvidence, CalibrationBudget


def observe(guard, expected=(0, 1), actual=(1, 0), **kwargs):
    return guard.observe(expected, actual, now=kwargs.pop('now', 1.), **kwargs)


def test_one_large_deviation_is_unknown_instead_of_proof_of_rotation():
    assert observe(DirectionEvidence())['state'] == 'unknown'


def test_wall_slide_does_not_contaminate_later_rotation_evidence():
    guard = DirectionEvidence()
    for i in range(4):
        assert observe(guard, geometry_clear=False, now=i)['state'] == 'obstacle_suspected'
    assert observe(guard)['state'] == 'unknown'


def test_consistent_independent_axes_form_a_candidate_not_a_grounded_fact():
    guard = DirectionEvidence()
    assert observe(guard, now=1)['state'] == 'unknown'
    assert observe(guard, now=2)['state'] == 'unknown'
    result = observe(guard, expected=(1, 0), actual=(0, -1), now=3)
    assert result['state'] == 'rotation_candidate'
    assert result['camera_observed'] is False


@pytest.mark.parametrize('kwargs', [{'height_delta': 1}, {'mode': 'jump'}, {'actual': (0, 0)}])
def test_jump_height_change_or_tiny_displacement_cannot_update_direction(kwargs):
    actual = kwargs.pop('actual', (1, 0))
    assert observe(DirectionEvidence(), actual=actual, **kwargs)['state'] == 'unknown'


def test_old_samples_and_scope_change_cannot_confirm_rotation():
    guard = DirectionEvidence()
    observe(guard, now=1, scope='a')
    observe(guard, now=2, scope='a')
    assert observe(guard, expected=(1, 0), actual=(0, -1), now=30, scope='a')['state'] == 'unknown'
    assert observe(guard, expected=(1, 0), actual=(0, -1), now=31, scope='b')['state'] == 'unknown'


def test_same_region_budget_does_not_reset_by_waiting_or_resuming():
    budget = CalibrationBudget()
    assert all(budget.begin((0, 0, 0), 'point', 'scene') for _ in range(3))
    assert not budget.begin((0, 0, 0), 'point', 'scene')
    assert budget.begin((5, 0, 0), 'point', 'scene')


def test_total_budget_prevents_moving_between_regions_from_resetting_forever():
    budget = CalibrationBudget()
    for i in range(6):
        assert budget.begin((i*10, 0, 0), 'point', 'scene')
    assert not budget.begin((80, 0, 0), 'point', 'scene')
    assert budget.begin((80, 0, 0), 'next-point', 'scene')


def test_scene_change_resets_calibration_scope_and_storage_stays_bounded():
    budget = CalibrationBudget()
    for _ in range(3):
        assert budget.begin((0, 0, 0), 1, 'old')
    assert budget.begin((0, 0, 0), 1, 'new')
    assert len(budget.regions) == 1
