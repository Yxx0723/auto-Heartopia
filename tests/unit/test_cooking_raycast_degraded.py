"""Replay gate: an unavailable UI-hit probe is degraded, a real hit stays strict."""

import pytest

from utils.flows.cooking_single_pot import start_raycast_rejection


UNAVAILABLE = {'state': 'unavailable', 'reason': 'no verified read-only UI hit result'}


def test_unavailable_probe_is_degraded_not_blocking():
    obs = {
        'start_raycast': dict(UNAVAILABLE),
        'unity_button': {'object': 42, 'm_Interactable': True, 'm_GroupsAllowInteraction': True},
        'center': (100, 50),
    }
    assert start_raycast_rejection(obs) == 'degraded'


def test_missing_probe_stays_blocked_fail_closed():
    # Only the explicit `unavailable` placeholder degrades. A missing probe
    # is not evidence, so replay must stay blocked.
    obs = {'unity_button': {'object': 42}, 'center': (100, 50)}
    assert start_raycast_rejection(obs) == '开始按钮的实际 UI 命中或遮挡状态未确认'


def test_real_hit_on_target_passes():
    obs = {
        'start_raycast': {'state': 'hit', 'active_in_hierarchy': True,
                          'canvas_allows_input': True, 'button': 42, 'point': (100, 50)},
        'unity_button': {'object': 42, 'm_Interactable': True, 'm_GroupsAllowInteraction': True},
        'center': (100, 50),
    }
    assert start_raycast_rejection(obs) is None


@pytest.mark.parametrize('patch', [
    {'state': 'miss'},
    {'state': 'hit', 'active_in_hierarchy': False},
    {'state': 'hit', 'active_in_hierarchy': True, 'canvas_allows_input': False},
    {'state': 'hit', 'active_in_hierarchy': True, 'canvas_allows_input': True, 'button': 99},
    {'state': 'hit', 'active_in_hierarchy': True, 'canvas_allows_input': True, 'button': 42, 'point': (1, 2)},
])
def test_real_hit_failure_stays_blocked(patch):
    hit = {'state': 'hit', 'active_in_hierarchy': True, 'canvas_allows_input': True,
           'button': 42, 'point': (100, 50)}
    hit.update(patch)
    obs = {
        'start_raycast': hit,
        'unity_button': {'object': 42, 'm_Interactable': True, 'm_GroupsAllowInteraction': True},
        'center': (100, 50),
    }
    assert start_raycast_rejection(obs) == '开始按钮的实际 UI 命中或遮挡状态未确认'


def test_interaction_and_raycast_are_independent():
    obs = {
        'start_raycast': dict(UNAVAILABLE),
        'unity_button': {'object': 42, 'm_Interactable': False, 'm_GroupsAllowInteraction': True},
        'center': (100, 50),
    }
    assert start_raycast_rejection(obs) == 'degraded'
