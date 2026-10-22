"""Two equal-cost corridors must not swap when the start jitters.

2026-10-21 reproduction at (-37.9, -62.7): a 5 cm change of standing position
flipped the first leg from +x to -x, so the character walked back and forth
inside a 10 cm box for 27 s and was finally skipped. The bias only raises the
cost of turning, so a detour the geometry actually demands still wins.
"""
from utils.core import thresholds as policy
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap, plan_path


def box(left, right, low, high, height=(-.5, 2.)):
    return {'polygon': [[left, low], [right, low], [right, high], [left, high]],
            'height': list(height)}


def scene(*boxes):
    return {'state': 'ready', 'objects': [], 'colliders': list(boxes)}


def test_equal_corridors_keep_one_side_once_a_heading_exists():
    # A centred block leaves two equally short ways around it.
    snapshot = scene(box(-1., 1., 3., 5.))
    target = (0., 0., 10.)
    firsts = []
    for x in (-.05, 0., .05):
        nav = LocalNavigator(lambda: snapshot, water_areas=())
        nav.next_target((-1., 0., 0.), target)       # anchor
        nav.next_target((-.5, 0., 0.), target)       # walked +x: heading = (+1, 0)
        assert nav.heading == (1., 0.)
        firsts.append(nav.next_target((x, 0., 0.), target))
    # The mirrored corridor is never chosen just because the start moved 5 cm.
    assert len({point[0] for point in firsts}) == 1, firsts


def test_heading_bias_still_lets_geometry_force_a_detour():
    snapshot = scene(box(-1., 1., 3., 5.), box(-6., -1., 3., 5.))  # only the left way
    obstacles = ObstacleMap(snapshot, (0., 0., 0.), water_areas=())
    straight = plan_path((0., 0., 0.), (0., 0., 10.), ObstacleMap(
        snapshot, (0., 0., 0.), water_areas=()))
    assert max(abs(point[0]) for point in straight) > 1.
    biased = plan_path((0., 0., 0.), (0., 0., 10.), obstacles, heading=(0., 1.))
    assert max(point[0] for point in biased) > 1.  # used the only open side
    assert biased[-1] == (0., 0., 10.)


def test_no_heading_keeps_the_old_behaviour():
    snapshot = scene(box(-1., 1., 3., 5.))
    obstacles = ObstacleMap(snapshot, (0., 0., 0.), water_areas=())
    plain = plan_path((0., 0., 0.), (0., 0., 10.), obstacles)
    assert plain[-1] == (0., 0., 10.)


def test_heading_only_penalises_turning_never_shortens_edges():
    assert policy.COORD_NAV_TURN_BIAS > 0
    assert policy.COORD_NAV_HEADING_MIN_MOVE > policy.COORD_REACH_RADIUS
