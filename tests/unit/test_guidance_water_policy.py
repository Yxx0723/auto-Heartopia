"""Explicit guidance-only water policy; no real input or water-depth inference."""
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_navigation import box, scene
from tests.unit.test_native_guidance_worker import FakeWindow, WaitingControls
from tests.unit.test_navigation_reference import reference
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait, ObstacleMap, plan_path
from utils.core.guidance_navigation import GuidanceNavigator
from utils.ui.point_playback import PointPlayback


START = (174.4557342529297, 25.027315139770508, 41.24953079223633)
AIM = (175.74711079875271, START[1], 41.35622085808707)


def product_navigator(source):
    runner = Mock(return_value={'arrived': False, 'reason': 'offline test'})
    worker = PointPlayback('profile.json', guidance_runner=runner, window_factory=FakeWindow,
        controls_factory=WaitingControls, device_factory=lambda w: Mock(),
        scene_provider=lambda pid: source)
    try:
        worker.start_guidance(77)
        worker.thread.join(timeout=2)
        assert not worker.active
        return runner.call_args.kwargs['navigator']
    finally:
        worker.close()


def test_actual_guidance_factory_accepts_recorded_water_target_without_shore_projection():
    nav = product_navigator(scene())
    assert nav.next_target(START, AIM) == AIM
    assert nav.obstacles.clear((START[0], START[2]), (AIM[0], AIM[2]))


def test_ordinary_route_still_rejects_same_water_target():
    with pytest.raises(NavigationWait):
        LocalNavigator(lambda: scene()).next_target(START, AIM)


@pytest.mark.parametrize('category', ['object', 'fence', 'handrail', 'mountain'])
def test_water_policy_does_not_remove_actual_solid_obstacles(category):
    wall = box(175.6, 175.9, 41., 41.7, (24., 28.))
    wall['category'] = category
    nav = product_navigator(scene(wall))
    with pytest.raises(NavigationWait):
        nav.next_target(START, AIM)


def test_guidance_does_not_detour_for_advisory_visual_water_either():
    source = scene()
    source['navigation_reference'] = reference()
    nav = product_navigator(source)
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.obstacles.risk_cost((0, 0), (0, 8)) == 0.
    path = plan_path((0, 0, 0), (0, 0, 8), nav.obstacles)
    assert path[-1] == (0., 0, 8.) and all(p[0] == 0. for p in path)


@pytest.mark.parametrize('kind', ['mountain', 'bridge'])
def test_other_reference_geometry_costs_are_preserved(kind):
    source = scene()
    source['navigation_reference'] = reference(kind, .8)
    nav = product_navigator(source)
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.obstacles.risk_cost((0, 0), (0, 8)) > 0.


def test_scene_loss_cannot_reuse_path_after_water_policy_change():
    source = scene()
    nav = product_navigator(source)
    nav.next_target(START, AIM)
    source['state'] = 'loading'
    with pytest.raises(NavigationWait) as exc:
        nav.next_target(START, AIM)
    assert exc.value.reason == 'scene'


def test_ordinary_reference_water_cost_is_unchanged():
    source = scene()
    source['navigation_reference'] = reference()
    nav = LocalNavigator(lambda: source, water_areas=())
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.obstacles.risk_cost((0, 0), (0, 8)) > 0.


def test_policy_is_explicit_and_logged_without_modifying_scene():
    source = scene()
    source['navigation_reference'] = reference()
    nav = product_navigator(source)
    assert nav.respect_water is False
    nav.next_target((0, 0, 0), (0, 0, 8))
    assert nav.last_timings['respect_water'] is False
    assert source['navigation_reference']['layers'][0]['kind'] == 'water'


def test_recovery_uses_same_water_policy_but_keeps_terrain_wall():
    source = scene()
    nav = product_navigator(source)
    assert nav.recovery_keys(START, AIM, {'forward': [1., 0.], 'right': [0., -1.]}, .3)
    assert nav.water_guard.clear((START[0], START[2]), (AIM[0], AIM[2]))


def test_recovery_terrain_wall_is_not_removed_with_water():
    wall = box(175.6, 175.9, 41., 41.7, (24., 28.))
    wall['source'] = 'verified-main-terrain-mesh'
    nav = product_navigator(scene(wall))
    nav.recovery_keys(START, AIM, {'forward': [1., 0.], 'right': [0., -1.]}, .3)
    assert not nav.water_guard.clear((START[0], START[2]), (AIM[0], AIM[2]))


def test_bridge_recognition_keeps_original_water_input():
    water = [box(-5, 5, 1, 4)['polygon']]
    source = scene(box(-.8, .8, -2, 7, (-.4, .2)))
    source['quality'] = {'navigation_eligible': True}
    obstacles = ObstacleMap(source, (0, 0, 0), water_areas=water, respect_water=False)
    assert len(obstacles.bridge_decks) == 1
    assert obstacles.water_polygon_ids == {}


def test_policy_cannot_be_mutated_behind_cached_map():
    nav = product_navigator(scene())
    with pytest.raises(AttributeError):
        nav.respect_water = True


@pytest.mark.parametrize('value', [None, 0, 'ignore'])
def test_invalid_policy_is_rejected(value):
    with pytest.raises(ValueError):
        GuidanceNavigator(lambda: scene(), respect_water=value)


def test_injected_navigator_factory_keeps_existing_constructor_contract():
    factory = Mock(return_value=LocalNavigator(lambda: scene(), water_areas=()))
    runner = Mock(return_value={'arrived': False, 'reason': 'offline'})
    worker = PointPlayback('profile.json', guidance_runner=runner, window_factory=FakeWindow,
        controls_factory=WaitingControls, device_factory=lambda w: Mock(),
        scene_provider=lambda pid: scene(), navigator_factory=factory)
    try:
        worker.start_guidance(77)
        worker.thread.join(timeout=2)
        assert not worker.active
        assert set(factory.call_args.kwargs) == {'clock'}
    finally:
        worker.close()
