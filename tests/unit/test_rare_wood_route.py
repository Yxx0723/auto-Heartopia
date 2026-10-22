"""Rare timber uses verified fixed trees, finite presses and fresh ownership."""
from copy import deepcopy
import struct
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import Game, manual_points
from utils.core.coordinate_points import ordered_waypoints, validate_points
from utils.core.coordinate_navigation import LocalNavigator
from utils.flows.coordinate_player import drive_points
from utils.drivers.rare_tree_gather import rare_tree_gather, NAMESPACE, DATA_NAMESPACE
from tools.build_rare_wood_route import build_route

STAMP = '2026-10-21T22:30:00+08:00'


def route():
    result = manual_points([(1.2, 1.05, 0)])
    result['points'][0].update(collect_count=3, collect_wait=.2, source={
        'kind': 'rare_wood', 'item_type': 211, 'resource_id': 1339,
        'source': 'main-map-resource-definition', 'stand_position': 'resource-center',
        'resource_position': [0., 0., 0.], 'height_offset': 1.05})
    return result


def scene():
    return {'state': 'ready', 'world_state': 'ready', 'colliders': [],
            'global_resource_state': 'ready', 'fixed_resource_context': {'map': 'main'},
            'position': [1.2, 1.05, 0.],
            'global_resources': [{'category': 'tree', 'position': [0., 0., 0.],
                                  'resource_id': 1339, 'item_type_id': 211, 'map_definition': True,
                                  'source': 'main-map-resource-definition'}],
            'objects': [{'category': 'tree', 'position': [0., 0., 0.], 'kind': 'entity',
                         'map_static': True, 'resource_id': 1339, 'resource_item_type': 211,
                         'resource_available': 3, 'resource_cold_end_ms': 0}]}


def play(game, data, snap, **options):
    return drive_points(data, game.size(), lambda: game, game, game, game, Mock(), clock=game,
                        navigator=LocalNavigator(lambda: snap, clock=game.monotonic, water_areas=()),
                        collect_wait_seconds=.2, skip_unreachable=True, **options)


def presses(game):
    return [t for t, e in game.events if e == {'type': 'key_down', 'key': 'f'}]


def test_three_presses_are_separated_and_do_not_walk_into_tree():
    game = Game(speed=0); game.position = [1.2, 1.05, 0.]
    data = route(); before = deepcopy(data)
    assert play(game, data, scene())
    times = presses(game)
    assert len(times) == 3 and all(b-a >= .31 for a, b in zip(times, times[1:]))
    assert not any(e.get('key') in ('w', 'a', 's', 'd', 'space') for _, e in game.events)
    assert data == before and not game.keys


def test_pause_during_a_press_does_not_duplicate_it():
    game = Game(speed=0); game.position = [1.2, 1.05, 0.]
    game.pause_at, game.resume_at = .3, 1.3
    assert play(game, route(), scene())
    assert len(presses(game)) == 3
    assert not [t for t in presses(game) if .3 <= t < 1.3] and not game.keys


@pytest.mark.parametrize('empty_after_first', [False, True])
def test_empty_tree_is_skipped_without_extra_presses(empty_after_first):
    game = Game(speed=0); game.position = [1.2, 1.05, 0.]
    snap = scene()
    if not empty_after_first:
        snap['objects'][0]['resource_available'] = 0
    else:
        def update(e):
            if e == {'type': 'key_down', 'key': 'f'}:
                snap['objects'][0]['resource_available'] = 0
        game.on_input = update
    assert play(game, route(), snap)
    assert len(presses(game)) == int(empty_after_first) and not game.keys


@pytest.mark.parametrize('fault', ['cached', 'definition', 'missing', 'foreign_id', 'ordinary_tree', 'unread_state'])
def test_unconfirmed_trees_never_authorize_f(fault):
    game = Game(speed=0); game.position = [1.2, 1.05, 0.]; game.stop_at = 20.
    snap = scene(); obj = snap['objects'][0]
    if fault == 'cached': obj['map_cached'] = True
    if fault == 'definition': obj['map_definition'] = True
    if fault == 'missing': snap['objects'] = []
    if fault == 'foreign_id': obj['resource_id'] += 1
    if fault == 'ordinary_tree': obj['resource_item_type'] = 206
    if fault == 'unread_state': obj.pop('resource_available')
    assert play(game, route(), snap)
    assert not presses(game) and not game.keys


def test_rare_wood_requires_live_navigation_before_any_input():
    game = Game()
    with pytest.raises(ValueError, match='自动寻路'):
        drive_points(route(), game.size(), lambda: game, game, game, game, Mock(), clock=game)
    assert not game.events


@pytest.mark.parametrize('bad', [0, 4, True, 2.5])
def test_invalid_rare_tree_press_counts_are_rejected(bad):
    data = route(); data['points'][0]['collect_count'] = bad
    with pytest.raises(ValueError): validate_points(data)


def test_builder_includes_every_rare_tree_once_and_preserves_scene():
    snap = scene()
    for i in range(1, 8):
        snap['global_resources'].append({**snap['global_resources'][0], 'resource_id': 1339-i,
                                         'position': [float(i*4), 0., 0.]})
    snap['global_resources'].append({**snap['global_resources'][0], 'item_type_id': 206, 'resource_id': 42})
    original = deepcopy(snap)
    data = build_route(snap, STAMP)
    assert snap == original
    assert len(data['points']) == 8 and {p['source']['resource_id'] for p in data['points']} == set(range(1332, 1340))
    assert all(p['collect_count'] == 3 and p['source']['item_type'] == 211 for p in ordered_waypoints(data))


def test_builder_refuses_unverified_main_world_and_nonunique_definitions():
    snap = scene(); snap['world_state'] = 'unavailable'
    with pytest.raises(ValueError): build_route(snap, STAMP)
    snap = scene(); snap['global_resources'] *= 2
    with pytest.raises(ValueError): build_route(snap, STAMP)


def runtime():
    meta = Mock()
    infos = {100: {'address': 1, 'name': 'CollectableTreeComponent', 'namespace': NAMESPACE, 'size': 64},
             300: {'address': 3, 'name': 'CollectableObjectComponent', 'namespace': NAMESPACE, 'size': 96}}
    classes = {10: {'address': 10, 'size': 20, 'name': 'TreeComponentData', 'namespace': DATA_NAMESPACE},
               20: {'address': 20, 'size': 40, 'name': 'CollectableObjectData', 'namespace': DATA_NAMESPACE}}
    def field(offset, code, typ=0): return {'offset': offset, 'code': code, 'flags': 6, 'type': typ}
    fields = {(1, '_itemTypeID'): field(48, 8), (1, '_componentData'): field(52, 17, 10),
              (10, 'resourceId'): field(16, 9), (3, 'itemTypeID'): field(80, 8),
              (3, '_componentData'): field(48, 17, 20), (20, 'availableNum'): field(28, 8),
              (20, 'coldEndTime'): field(16, 10)}
    refs = {(500, '<entity>k__BackingField'): 400, (400, '_components'): 600,
            (100, '<entity>k__BackingField'): 400, (300, '<entity>k__BackingField'): 400, (300, '_proxy'): 100}
    meta.ref.side_effect = lambda obj, name: refs[obj, name]
    meta.obj.side_effect = infos.__getitem__; meta.cls.side_effect = classes.__getitem__
    meta.field.side_effect = lambda ci, name: fields[ci, name]
    meta.dictionary.return_value = [100, 300]; meta.integer.return_value = 211
    meta.data.side_effect = lambda at, n: struct.pack('<I', 1339) if at == 152 else struct.pack('<qfi8x', 0, 0., 3)
    return meta, fields, refs


def test_reader_binds_tree_resource_id_and_owned_availability():
    meta, _, _ = runtime()
    assert rare_tree_gather(meta, 500) == {'resource_id': 1339, 'resource_item_type': 211,
                                         'resource_available': 3, 'resource_cold_end_ms': 0}


@pytest.mark.parametrize('fault', ['foreign_owner', 'foreign_proxy', 'static_id_field', 'changing_container'])
def test_reader_rejects_ownership_and_layout_changes(fault):
    meta, fields, refs = runtime()
    if fault == 'foreign_owner': refs[100, '<entity>k__BackingField'] = 999
    if fault == 'foreign_proxy': refs[300, '_proxy'] = 999
    if fault == 'static_id_field': fields[10, 'resourceId']['flags'] |= 16
    if fault == 'changing_container': meta.dictionary.side_effect = [[100, 300], [100]]
    with pytest.raises(ValueError): rare_tree_gather(meta, 500)
