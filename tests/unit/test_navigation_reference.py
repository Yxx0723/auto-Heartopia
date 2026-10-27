"""Synthetic offline geometry, never a gameplay or grounded-state test."""
from copy import deepcopy
import json

import pytest

from tests.unit.test_coordinate_navigation import scene, box
from utils.core.coordinate_navigation import LocalNavigator, ObstacleMap, plan_path
from utils.core.navigation_reference import load_reference, validate_reference


BUILD = 'a' * 64


def reference(kind='water', y=0.):
    value = {'schema': 'navigation-reference-v1', 'game_hash': BUILD,
             'scene': 'startown', 'bounds_xz': [-20., 20., -20., 20.],
             'layers': [{'kind': kind, 'source': 'synthetic-unverified-source',
                         'vertices': [[-2., y, 2.], [2., y, 2.], [2., y, 4.], [-2., y, 4.]],
                         'triangles': [[0, 1, 2], [0, 2, 3]]}]}
    return json.loads(json.dumps(validate_reference(value)))


def mapped(ref):
    value = scene()
    value['navigation_reference'] = ref
    return value


def test_new_water_mesh_influences_search_and_shortcuts_without_becoming_a_hard_wall():
    obstacles = ObstacleMap(mapped(reference()), (0, 0, 0), water_areas=())
    assert obstacles.clear((0, 0), (0, 8))
    assert obstacles.risk_cost((0, 0), (0, 8)) > 0
    path = plan_path((0, 0, 0), (0, 0, 8), obstacles)
    assert max(abs(p[0]) for p in path) > 2.
    points = [(0, 0)] + [(p[0], p[2]) for p in path]
    # Finite advisory costs can prefer a very small corner exposure over a
    # huge detour. They must not disappear in simplification or steering.
    assert sum(obstacles.risk_cost(a, b) for a, b in zip(points, points[1:])) \
        < .1 * obstacles.risk_cost((0, 0), (0, 8))


def test_unverified_bridge_mesh_never_unlocks_known_water():
    obstacles = ObstacleMap(mapped(reference('bridge')), (0, 0, 0),
                            water_areas=(box(low=2, high=4)['polygon'],))
    assert not obstacles.clear((0, 0), (0, 8))


@pytest.mark.parametrize('kind', ['mountain', 'bridge'])
def test_source_wall_is_height_clipped_and_does_not_seal_another_floor(kind):
    data = reference(kind)
    # Vertical source triangle crossing the body, no active collider association.
    data['layers'][0]['vertices'] = [[-2., -1., 3.], [2., -1., 3.], [0., 3., 3.], [0., 3., 3.]]
    data['layers'][0]['triangles'] = [[0, 1, 2]]
    data = validate_reference(data)
    assert ObstacleMap(mapped(data), (0, 0, 0), water_areas=()).risk_cost((0, 0), (0, 8)) > 0
    assert ObstacleMap(mapped(data), (0, 5, 0), water_areas=()).risk_cost((0, 0), (0, 8)) == 0
    # A floor below the feet must not turn into a solid body obstacle.
    assert ObstacleMap(mapped(reference(kind, -2)), (0, 0, 0), water_areas=()).risk_cost((0, 0), (0, 8)) == 0


def test_water_at_another_level_is_not_applied_to_this_level():
    assert ObstacleMap(mapped(reference()), (0, 8, 0), water_areas=()).risk_cost((0, 0), (0, 8)) == 0


def test_triangle_subdivision_or_duplicates_do_not_multiply_cost():
    data = reference()
    original = ObstacleMap(mapped(data), (0, 0, 0), water_areas=())
    data = deepcopy(data)
    data['layers'][0]['triangles'] *= 4
    data = validate_reference(data)
    duplicate = ObstacleMap(mapped(data), (0, 0, 0), water_areas=())
    assert duplicate.risk_cost((0, 0), (0, 8)) == pytest.approx(original.risk_cost((0, 0), (0, 8)))
    assert duplicate.risk_cost((0, 0), (0, 8)) == pytest.approx(
        duplicate.risk_cost((0, 0), (0, 3)) + duplicate.risk_cost((0, 3), (0, 8)))


def test_reference_change_invalidates_cached_path_even_when_scene_object_is_reused():
    data = scene()
    nav = LocalNavigator(lambda: data, water_areas=())
    assert nav.next_target((0, 0, 0), (0, 0, 8))[0] == 0
    data['navigation_reference'] = reference()
    assert abs(nav.next_target((0, 0, 0), (0, 0, 8))[0]) > .1
    data.pop('navigation_reference')
    assert nav.next_target((0, 0, 0), (0, 0, 8))[0] == 0


def test_confirmed_wall_and_navigation_exclusions_remain_hard_constraints():
    data = mapped(reference())
    data['colliders'] = [box()]
    assert not ObstacleMap(data, (0, 0, 0), water_areas=()).clear((0, 0), (0, 8))


def test_build_and_scene_gates_and_corrupt_file_fallback(tmp_path):
    path = tmp_path / 'reference.json'
    path.write_text(json.dumps(reference()), encoding='utf-8')
    assert load_reference(path, BUILD, True)
    assert load_reference(path, 'b' * 64, True) is None
    assert load_reference(path, BUILD, False) is None
    assert load_reference(path, BUILD, True, scene_name='interior') is None
    path.write_text('{broken', encoding='utf-8')
    assert load_reference(path, BUILD, True) is None


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(schema='unknown'),
    lambda d: d.update(game_hash=''),
    lambda d: d['layers'][0]['vertices'][0].__setitem__(0, float('nan')),
    lambda d: d['layers'][0]['triangles'][0].__setitem__(0, -1),
    lambda d: d['layers'][0].update(kind='confirmed-wall'),
])
def test_invalid_reference_rejected(mutate):
    value = reference()
    mutate(value)
    with pytest.raises(ValueError):
        validate_reference(value)


def test_recognized_live_deck_discounts_water_risk_only_at_its_own_height():
    deck = box(-2, 2, 0, 10, (-.3, 0))
    deck['source'] = 'verified-native-world-collider'
    data = mapped(reference())
    data.update(colliders=[deck], quality={'navigation_eligible': True})
    water = (box(-3, 3, 2, 4)['polygon'],)
    above = ObstacleMap(data, (0, 0, 0), water_areas=water)
    assert len(above.bridge_decks) == 1
    assert above.clear((0, 0), (0, 8))
    assert above.risk_cost((0, 0), (0, 8)) == 0
    below = ObstacleMap(data, (0, -3, 0), water_areas=water)
    assert not below.bridge_decks
    assert not below.clear((0, 0), (0, 8))


def test_route_memory_cannot_skip_source_risks_or_rewrite_saved_points(tmp_path):
    from tests.unit.test_coordinate_player import manual_points
    from utils.drivers.route_path_memory import RoutePathMemory
    points = manual_points([(0, 0, 8)])
    memory = RoutePathMemory(tmp_path/'route.json', points)
    assert memory.remember(0, [[0., 0., float(z)] for z in range(9)])
    before = memory.path.read_bytes()
    nav = LocalNavigator(lambda: mapped(reference()), water_areas=(), path_memory=memory)
    nav.set_waypoint(0)
    position, walked = (0, 0, 0), []
    for _ in range(30):
        selected = nav.next_target(position, (0, 0, 8))
        if nav.memory_state == 'reusing':
            assert nav.obstacles.risk_cost((position[0], position[2]), (selected[0], selected[2])) == 0
        walked.append(selected)
        position = selected
        if position == (0, 0, 8):
            break
    assert position == (0, 0, 8)
    assert max(abs(p[0]) for p in walked) > 2.
    assert memory.path.read_bytes() == before


def test_stopping_during_search_is_not_swallowed_as_no_path():
    calls = []
    def stop():
        calls.append(1)
        if len(calls) > 2:
            raise RuntimeError('synthetic stop')
    obstacles = ObstacleMap(mapped(reference()), (0, 0, 0), water_areas=())
    released = []
    with pytest.raises(RuntimeError, match='synthetic stop'):
        plan_path((0, 0, 0), (0, 0, 8), obstacles, check=stop, before_search=lambda: released.append(True))
    assert released


def test_oversized_file_is_rejected_without_decoding(tmp_path, monkeypatch):
    from utils.core import thresholds as policy
    monkeypatch.setattr(policy, 'COORD_REFERENCE_MAX_BYTES', 16)
    path = tmp_path/'too-large.json'
    path.write_text(json.dumps(reference()), encoding='utf-8')
    assert load_reference(path, BUILD, True) is None


def test_reference_data_dir_uses_executable_directory_in_frozen_build(tmp_path, monkeypatch):
    import sys
    from utils.core.navigation_reference import default_reference_path
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path/'monitor.exe'))
    assert default_reference_path() == tmp_path/'runtime/navigation/two-lakes.json'


def test_geometry_not_in_local_window_has_no_risk_or_index_entries():
    obstacles = ObstacleMap(mapped(reference()), (1000, 0, 1000), water_areas=())
    assert not obstacles.reference_risks.entries
    assert not obstacles.reference_risks.bins


def test_reference_cost_cache_is_bounded_and_does_not_cache_directional_failures(monkeypatch):
    from utils.core import thresholds as policy
    from utils.core.approach_risk import ApproachRisks
    monkeypatch.setattr(policy, 'COORD_REFERENCE_COST_CACHE_LIMIT', 2)
    risks = ApproachRisks(clock=lambda: 0.)
    obstacles = ObstacleMap(mapped(reference()), (0, 0, 0), water_areas=(), risks=risks)
    baseline = obstacles.risk_cost((0, 0), (0, 8))
    for i in range(10):
        obstacles.risk_cost((i, 0), (i, 8))
    assert len(obstacles.reference_risks.cost_cache) <= 2
    risks.record((0, 0, 0), (0, 0, 4))
    assert obstacles.risk_cost((0, 0), (0, 8)) > baseline
    assert obstacles.risk_cost((0, 8), (0, 0)) == pytest.approx(baseline)
