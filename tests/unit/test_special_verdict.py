"""A press is judged from read-only samples; only real state changes count."""
import pytest
from utils.core import special_verdict as verdict


BEFORE = {'available': 2, 'cold_end': 0, 'digest': 'a'}


def test_a_moved_collectable_counter_is_the_only_confirmed_drop():
    assert verdict.verdict(BEFORE, {'available': 1, 'cold_end': 0, 'digest': 'a'}) == verdict.GATHERED
    assert verdict.confirmed(verdict.GATHERED)


def test_a_vanished_target_counts_as_collected():
    assert verdict.verdict(BEFORE, None) == verdict.GONE
    assert verdict.confirmed(verdict.GONE)


def test_a_forward_cold_end_is_probable_not_proven():
    outcome = verdict.verdict(BEFORE, {'available': 2, 'cold_end': 5, 'digest': 'a'})
    assert outcome == verdict.COOLING and not verdict.confirmed(outcome)
    assert verdict.conclusive(outcome)


def test_an_unparsed_state_field_never_becomes_a_drop_claim():
    assert verdict.verdict(BEFORE, {'available': 2, 'cold_end': 0, 'digest': 'b'}) == verdict.CHANGED
    assert verdict.verdict(BEFORE, {'available': None, 'cold_end': 0, 'digest': 'a'}) == verdict.UNKNOWN
    assert not verdict.confirmed(verdict.CHANGED) and not verdict.confirmed(verdict.UNKNOWN)


def test_identical_samples_are_unchanged_and_worth_retrying():
    assert verdict.verdict(BEFORE, dict(BEFORE)) == verdict.UNCHANGED
    assert not verdict.conclusive(verdict.UNCHANGED)
    assert verdict.verdict(BEFORE, {'available': 2, 'cold_end': 0}) == verdict.UNCHANGED


def test_a_digest_only_change_is_not_settled():
    assert not verdict.conclusive(verdict.CHANGED)


@pytest.mark.parametrize('target', [{}, None])
def test_snapshot_tolerates_missing_samples(target):
    assert verdict.snapshot(target) is None


def test_a_later_quiet_sample_cannot_erase_an_observed_change():
    assert verdict.strongest([verdict.CHANGED, verdict.UNCHANGED]) == verdict.CHANGED
    assert verdict.strongest([verdict.UNCHANGED, verdict.GATHERED]) == verdict.GATHERED
    assert verdict.strongest([verdict.UNCHANGED, verdict.UNCHANGED]) == verdict.UNCHANGED
    assert verdict.strongest([]) == verdict.UNKNOWN
