"""Learned stall walls: an empty scene that the game still refuses to cross.

The 2026-10-21 ore run stalled at (-84.4, -134.8) with no modelled wall within
6 units and nothing readable in the live snapshot, so no amount of scene data
could explain it. These tests pin the replacement for the old short-lived disc:
a wall across the attempted line that widens where stalls repeat.
"""
from utils.core import thresholds as policy
from utils.core.coordinate_navigation import LocalNavigator


def scene():
    return {'state': 'ready', 'objects': [], 'colliders': []}


def navigator(clock=lambda: 1000.):
    return LocalNavigator(scene, clock=clock, water_areas=())


def bounds(polygon):
    xs = [p[0] for p in polygon]
    zs = [p[1] for p in polygon]
    return min(xs), max(xs), min(zs), max(zs)


def test_stall_records_a_wall_across_the_attempted_line():
    nav = navigator()
    evidence = nav.blocked((0, 0, 0), (0, 0, 9))
    assert len(nav.probes) == 1
    left, right, low, high = bounds(nav.probes[0][1])
    half = policy.COORD_NAV_BLOCK_HALF_WIDTH
    assert abs((right - left) - half * 2) < .01  # a wall of body-scale width
    assert left < -.5 and right > .5  # spans the walking line, not a small disc
    assert low >= policy.COORD_NAV_BLOCK_DISTANCE - policy.COORD_NAV_BLOCK_THICKNESS
    assert high <= policy.COORD_NAV_BLOCK_DISTANCE + policy.COORD_NAV_BLOCK_THICKNESS
    assert evidence['learned_block']['level'] == 1
    assert nav.blocked_points == [[0, 0, 1]]


def test_wall_is_ahead_of_the_character_not_around_it():
    nav = navigator()
    nav.blocked((0, 0, 0), (0, 0, 9))
    # Standing still is never blocked, so the character can always release itself.
    assert nav.obstacles is None  # invalidated by blocked()
    nav.next_target((0, 0, 0), (0, 0, 9))
    assert nav.obstacles.clear((0, 0), (0, 0), escape=True)


def test_repeated_stall_at_the_same_spot_widens_the_wall():
    nav = navigator()
    nav.blocked((0, 0, 0), (0, 0, 9))
    first = nav.probes[0][1]
    evidence = nav.blocked((.4, 0, .3), (0, 0, 9))
    assert evidence['learned_block']['level'] == 2
    assert len(nav.probes) == 2
    left, right, _, _ = bounds(nav.probes[-1][1])
    assert right - left > bounds(first)[1] - bounds(first)[0] + policy.COORD_NAV_BLOCK_WIDTH_STEP - .01


def test_disjoint_stalls_each_start_at_level_one():
    nav = navigator()
    assert nav.blocked((0, 0, 0), (0, 0, 9))['learned_block']['level'] == 1
    far = policy.COORD_NAV_BLOCK_ESCALATE_RADIUS + 5
    assert nav.blocked((far, 0, far), (far + 1, 0, far + 6))['learned_block']['level'] == 1
    assert len(nav.blocked_points) == 2


def test_level_is_capped_so_a_corner_cannot_grow_without_bound():
    nav = navigator()
    for _ in range(policy.COORD_NAV_BLOCK_MAX_LEVEL + 4):
        level = nav.blocked((0, 0, 0), (0, 0, 9))['learned_block']['level']
    assert level == policy.COORD_NAV_BLOCK_MAX_LEVEL
    left, right, _, _ = bounds(nav.probes[-1][1])
    widest = policy.COORD_NAV_BLOCK_HALF_WIDTH + (policy.COORD_NAV_BLOCK_MAX_LEVEL - 1) \
        * policy.COORD_NAV_BLOCK_WIDTH_STEP
    assert abs((right - left) - widest * 2) < .01


def test_learned_wall_makes_the_planner_detour_instead_of_repeating_the_line():
    nav = navigator()
    straight = nav.next_target((0, 0, 0), (0, 0, 12))
    assert straight[0] == 0  # nothing known yet: it aims straight at the target
    nav.blocked((0, 0, 0), (0, 0, 12))
    nav.next_target((0, 0, 0), (0, 0, 12))
    assert not nav.obstacles.clear((0, 0), (0, 12))
    lateral = max(abs(point[0]) for point in nav.path)
    assert lateral > policy.COORD_NAV_BLOCK_HALF_WIDTH


def test_learned_wall_survives_a_fresh_map_but_not_a_world_change():
    snapshot = scene()
    nav = navigator()
    nav.blocked((0, 0, 0), (0, 0, 9))
    nav.next_target((0, 0, 0), (0, 0, 9))
    assert nav.probes and nav.blocked_points
    other = dict(snapshot, cache_context=('pid', 'another-world'))
    nav.scene_provider = lambda: other
    nav.next_target((0, 0, 0), (0, 0, 9))
    assert not nav.probes and not nav.blocked_points


def test_learned_wall_lasts_a_whole_run_instead_of_seconds():
    now = 500.
    nav = navigator(clock=lambda: now)
    nav.blocked((0, 0, 0), (0, 0, 9))
    assert nav.probes[0][0] - now == policy.COORD_NAV_BLOCK_SECONDS
    assert policy.COORD_NAV_BLOCK_SECONDS >= policy.COORD_POINT_PLAYBACK_SECONDS
