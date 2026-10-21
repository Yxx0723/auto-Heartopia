"""Large detours, partial plans and world changes use no game or device APIs."""
import pytest

from tests.unit.test_coordinate_navigation import box, scene, assert_clear
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap, NavigationWait, plan_path
from tests.unit.test_coordinate_navigation import NavigationGame
from tests.unit.test_coordinate_player import manual_points
from utils.core.coordinate_route import distance
from utils.flows.coordinate_player import drive_points


def navigator(*args, **kwargs):
    # Search semantics are independent of CI machine load; deadline handling
    # gets its own advancing-clock test below. Node budgets always apply.
    return LocalNavigator(*args, planning_clock=kwargs.pop('planning_clock', lambda: 0.), **kwargs)


def test_complete_wider_segment_wins_over_a_closer_partial_frontier():
    wall = box(-35, 35, 12, 15)
    nav = navigator(lambda: scene(wall), water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 100))
    assert nav.search_radius == 60.
    assert nav.path[-1] == (0., 0, 48.)
    assert_clear(nav.path, nav.obstacles)


def test_large_dead_end_can_first_walk_away_from_target_to_escape():
    walls = [box(-85, 85, 4, 5), box(-85, -84, -85, 5), box(84, 85, -85, 5)]
    nav = navigator(lambda: scene(*walls), water_areas=())
    next_point = nav.next_target((0, 0, 0), (0, 0, 8))
    assert next_point[2] < 0
    assert min(p[2] for p in nav.guide_path) < -85.6
    assert nav.guide_path[-1] == (0., 0, 8.)
    assert_clear(nav.path, nav.obstacles)


def test_long_river_uses_existing_gap_outside_the_local_search_window():
    water = [box(-200, 80, 10, 18)['polygon'], box(86, 200, 10, 18)['polygon']]
    nav = navigator(lambda: scene(), water_areas=water)
    nav.next_target((0, 0, 0), (0, 0, 40))
    assert nav.guide_path and max(p[0] for p in nav.guide_path) > 80
    all_water = ObstacleMap(scene(), (0, 0, 0), water_areas=water, search_radius=240)
    assert_clear(nav.guide_path, all_water)
    assert nav.guide_path[-1] == (0., 0, 40.)


def test_world_switch_discards_inferred_obstacles_and_guidance():
    current = [{**scene(), 'cache_context': 'world-a'}]
    nav = navigator(lambda: current[0], water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 8))
    nav.blocked((0, 0, 0), (0, 0, 3))
    assert nav.probes
    current[0] = {**scene(), 'cache_context': 'world-b'}
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.probes == []
    assert all(p[0] == 0 for p in nav.path)


def test_optional_resource_scan_failure_is_not_a_native_world_switch():
    current = [{**scene(), 'cache_context': (7, 'same-world'),
                'global_special_context': (7, 111, 222), 'terrain_revision': 'v1'}]
    nav = navigator(lambda: current[0], water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 8))
    nav.blocked((0, 0, 0), (0, 0, 3))
    current[0] = {**scene(), 'cache_context': (7, 'same-world'),
                  'global_special_context': None, 'terrain_revision': None}
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.probes


def test_search_budget_is_not_reported_as_proven_no_path():
    obstacles = ObstacleMap(scene(box()), (0, 0, 0), water_areas=())
    with pytest.raises(NavigationWait) as error:
        plan_path((0, 0, 0), (0, 0, 8), obstacles, max_nodes=1)
    assert error.value.reason == 'budget'


def test_f9_interruption_propagates_from_the_large_search():
    walls = [box(-85, 85, 4, 5), box(-85, -84, -85, 5), box(84, 85, -85, 5)]
    nav = navigator(lambda: scene(*walls), water_areas=())
    searches = []
    def release_and_check():
        searches.append(True)
        if len(searches) == 4:
            raise RuntimeError('F9 stopped')
    with pytest.raises(RuntimeError, match='F9 stopped'):
        nav.next_target((0, 0, 0), (0, 0, 8), before_plan=release_and_check)


def test_guide_reuse_never_turns_back_to_already_passed_corridor_points():
    walls = [box(-85, 85, 4, 5), box(-85, -84, -85, 5), box(84, 85, -85, 5)]
    nav = navigator(lambda: scene(*walls), water_areas=())
    destination = (0, 0, 8)
    nav.next_target((0, 0, 0), destination)
    position = nav.path[-1]
    assert position[2] < -15
    following = nav.next_target(position, destination)
    assert following[2] < position[2]
    assert nav.guide_stats['plans'] == 1


def test_closed_loop_large_detour_finishes_without_crossing_water_or_collecting_on_the_way():
    water = [box(-200, 80, 10, 18)['polygon'], box(86, 200, 10, 18)['polygon']]
    game = NavigationGame(physical=water)
    nav = navigator(lambda: scene(), clock=game.monotonic, water_areas=water)
    collected = []
    def report(state, message, **extra):
        if state == 'collecting':
            collected.append(game.read())
    target = (0, 0, 40)
    assert drive_points(manual_points([target]), game.size(), lambda: game, game, game, game,
                        report, clock=game, navigator=nav, collect_wait_seconds=0.)
    assert len(collected) == 1 and distance(collected[0], target) <= .3
    assert game.collisions == 0 and not game.keys and game.closed


def test_new_local_obstacle_is_rechecked_before_using_cached_large_guide():
    water = [box(-200, 80, 10, 18)['polygon'], box(86, 200, 10, 18)['polygon']]
    current = [scene()]
    nav = navigator(lambda: current[0], water_areas=water)
    target = (0, 0, 40)
    nav.next_target((0, 0, 0), target)
    guide_plans = nav.guide_stats['plans']
    position = nav.path[0]
    current[0] = scene(box(5, 6, -2, 2))
    nav.next_target(position, target)
    assert_clear(nav.path, nav.obstacles, position)
    assert nav.guide_stats['plans'] == guide_plans


def test_mountain_classification_change_removes_old_terminal_padding():
    trunk = box(-.25, .25, 7.7, 8.2)
    current = [scene(trunk)]
    nav = navigator(lambda: current[0], water_areas=())
    target = (0, 0, 8.35)
    nav.next_target((0, 0, 0), target)
    assert nav.obstacles.terminal_padding
    current[0] = scene({**trunk, 'category': 'mountain'})
    with pytest.raises(NavigationWait):
        nav.next_target((0, 0, 0), target)
    assert not nav.obstacles.terminal_padding


def test_large_search_budget_never_publishes_an_incomplete_guide(monkeypatch):
    from utils.core import thresholds
    monkeypatch.setattr(thresholds, 'COORD_NAV_GUIDE_MAX_NODES', 1)
    walls = [box(-85, 85, 4, 5), box(-85, -84, -85, 5), box(84, 85, -85, 5)]
    nav = navigator(lambda: scene(*walls), water_areas=())
    with pytest.raises(NavigationWait) as error:
        nav.next_target((0, 0, 0), (0, 0, 8))
    assert error.value.reason == 'budget'
    assert nav.guide_path == [] and nav.path == []


def test_fine_large_search_finds_a_narrow_gap_missed_by_the_coarse_grid():
    # The 0.8-unit padded opening has no 1/3-unit grid column. A .75-unit
    # column exists beyond the normal 32-unit window, within the node budget.
    water = [box(-200, 33.1, 10, 18)['polygon'], box(35.9, 200, 10, 18)['polygon']]
    nav = navigator(lambda: scene(), water_areas=water)
    nav.next_target((0, 0, 0), (0, 0, 40))
    assert nav.guide_stats['plans'] == 2
    obstacles = ObstacleMap(scene(), (0, 0, 0), water_areas=water, search_radius=240)
    assert_clear(nav.guide_path, obstacles)
    assert nav.guide_path[-1] == (0., 0, 40.)


def test_wall_clock_budget_cancels_large_search_without_a_partial_guide():
    walls = [box(-85, 85, 4, 5), box(-85, -84, -85, 5), box(84, 85, -85, 5)]
    now = [0.]
    def cpu_clock():
        now[0] += .4
        return now[0]
    nav = navigator(lambda: scene(*walls), water_areas=(), planning_clock=cpu_clock)
    released = []
    with pytest.raises(NavigationWait) as error:
        nav.next_target((0, 0, 0), (0, 0, 8), before_plan=lambda: released.append(True))
    assert error.value.reason == 'budget' and nav.guide_state == 'budget'
    assert not nav.guide_path and not nav.path and len(released) == 4
    with pytest.raises(NavigationWait) as waiting:
        nav.next_target((0, 0, 0), (0, 0, 8))
    assert waiting.value.reason == 'budget'
