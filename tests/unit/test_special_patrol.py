"""Patrol plans are search areas, never synthetic collectible entities."""
import pytest
from utils.core.special_patrol import patrol_checkpoints, patrol_target, make_patrol_data


def fixed(x, z, y=20., **kw):
    return {'position': [x, y, z], 'category': 'tree', 'map_definition': True, **kw}


def live(**kw):
    return {'category': 'roaming_oak', 'resource_static_id': 42001, 'map_dynamic': True,
            'position': [1., 20., 2.], 'resource_available': 3, **kw}


def test_plan_uses_distributed_fixed_definitions_without_mutating_them():
    objs = [fixed(2, 2), fixed(3, 3), fixed(85, 2), fixed(2, 85),
            fixed(200, 200, category='bird'), fixed(300, 300, map_definition=False)]
    scene = {'global_resources': objs}
    points = patrol_checkpoints(scene)
    assert len(points) == 3
    assert all(len(p) == 3 for p in points)
    assert objs[0]['position'] == [2, 20., 2]
    assert patrol_checkpoints(scene) == points


def test_no_fauna_or_unverified_cached_object_creates_a_patrol_plan():
    assert patrol_checkpoints({'objects': [live()], 'global_resources': [fixed(0, 0, map_definition=False)]}) == []


def test_live_usable_object_preempts_cached_search_position():
    target = live()
    scene = {'state': 'ready', 'objects': [target], 'global_special_state': 'ready',
             'special_search_hints': [live(resource_search_hint=True, resource_global_only=True)]}
    assert patrol_target(scene, 'roaming_oak', [target['position']]) == ('live', target)


def test_rejected_or_unavailable_target_is_not_revisited_from_old_hint():
    pos = [1., 20., 2.]
    hint = live(resource_search_hint=True, resource_global_only=True)
    scene = {'state': 'ready', 'objects': [live(resource_available=0)],
             'global_special_state': 'ready', 'special_search_hints': [hint]}
    assert patrol_target(scene, 'roaming_oak', []) == ('unavailable', scene['objects'][0])
    scene['objects'] = []
    assert patrol_target(scene, 'roaming_oak', [pos]) == ('search', None)


def test_partial_and_ambiguous_live_identity_never_grants_collection():
    assert patrol_target({'state': 'loading'}, 'roaming_oak', []) == ('wait', None)
    assert patrol_target({'state': 'ready', 'objects': [live(), live()]}, 'roaming_oak', [])[0] == 'wait'
    assert patrol_target({'state': 'ready', 'objects': [live(resource_available=None)]}, 'roaming_oak', [])[0] == 'wait'


def test_patrol_can_start_without_a_loaded_target_and_validates_its_identity():
    from utils.core.coordinate_points import validate_points
    scene = {'state': 'ready', 'objects': [], 'global_resources': [fixed(2, 2)]}
    data = make_patrol_data(scene, [0., 20., 0.], 'roaming_oak', '溜溜橡木', 3,
                            '2026-10-21T12:00:00+08:00')
    validate_points(data)
    assert data['special_patrol']['checkpoints']
    assert data['points'][0]['special_resource']['static_id'] == 42001
    data['special_patrol']['static_id'] = 42002
    with pytest.raises(ValueError):
        validate_points(data)


def test_live_target_can_start_while_no_survey_definitions_are_loaded():
    data = make_patrol_data({'state': 'ready', 'objects': [live()]}, [0., 20., 0.],
                            'roaming_oak', '溜溜橡木', 3, '2026-10-21T12:00:00+08:00')
    assert data['points'][0]['position'] == [1., 20., 2.]
