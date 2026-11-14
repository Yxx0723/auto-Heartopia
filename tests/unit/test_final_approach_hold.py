"""Final approach holds the press until the point is inside the reach radius.

Releasing every iteration between approach and reach radius made the last
fraction of a unit a 0.05 s press / 0.08 s release creep, which the no-progress
window then reported as a movement stall. These tests pin the hold behaviour and
the unchanged arrival stop.
"""
from unittest.mock import Mock

from utils.core import thresholds as policy
from utils.flows.coordinate_player import _Follower


def player(speed=3.):
    instance = _Follower.__new__(_Follower)
    instance.calibration_speed = speed
    instance.clock = Mock()
    instance.release = Mock()
    instance.wait = Mock()
    return instance


def test_approach_outside_reach_radius_keeps_the_keys_pressed():
    instance = player()
    instance.wait_movement(0., 1.2, True)
    instance.clock.sleep.assert_called_once()
    # No release and no settle wait: the character keeps walking the last metre.
    instance.release.assert_not_called()
    instance.wait.assert_not_called()


def test_just_outside_reach_radius_still_holds_the_press():
    instance = player()
    instance.wait_movement(0., policy.COORD_REACH_RADIUS + .02, True)
    instance.release.assert_not_called()
    instance.wait.assert_not_called()


def test_arrival_still_releases_and_settles():
    instance = player()
    instance.wait_movement(0., policy.COORD_REACH_RADIUS, True)
    instance.release.assert_called_once_with()
    instance.wait.assert_called_once_with(policy.COORD_NAV_APPROACH_SETTLE_SECONDS)


def test_sample_sleep_is_capped_by_the_loop_cadence():
    instance = player()
    instance.wait_movement(0., 6., True)
    instance.clock.sleep.assert_called_once_with(policy.COORD_SAMPLE_SECONDS)


def test_short_final_distance_still_waits_a_minimum_interval():
    instance = player(speed=3.)
    instance.wait_movement(0., .02, True)
    instance.clock.sleep.assert_called_once_with(.01)


def test_non_final_movement_never_releases():
    instance = player()
    instance.wait_movement(0., 10., False)
    instance.clock.sleep.assert_called_once_with(policy.COORD_SAMPLE_SECONDS)
    instance.release.assert_not_called()
    instance.wait.assert_not_called()
