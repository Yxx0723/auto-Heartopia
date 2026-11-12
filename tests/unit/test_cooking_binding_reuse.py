from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.drivers.cooking_single_pot import SinglePotReader


STOVE = (11, 22, 33)


class Scene:
    def __init__(self):
        self.registry = 40
        self.context = (1, 2, 3)
        self.generation = {40: 1, 70: 1}
        self.collections = {40: [50], 70: [80]}
        self.enumerations = []
        self.refs = {(50, '_hierarchyObject'): 60, (60, '<entity>k__BackingField'): 61,
                     (61, '_components'): 70, (80, '_parentCookerComponent'): 81,
                     (81, '<entity>k__BackingField'): 82}

    def obj(self, address):
        name, namespace = {
            60: ('StaticLevelObjectWrap', ''),
            80: ('CookingComponent', 'XDTLevelAndEntity.Gameplay.Component.Homeland'),
        }.get(address, ('Other', ''))
        return {'name': name, 'namespace': namespace, 'address': address}

    def cls(self, address):
        return {'address': address, 'name': 'CookingComponentData',
                'namespace': 'XDTDataAndProtocol.ComponentsData'}

    def field(self, address, name):
        return {'type': 90, 'code': 17, 'offset': 16}

    def data(self, address, size):
        return (33).to_bytes(size, 'little')

    def static_ref(self, owner, name):
        assert name == 'ActiveHierarchies'
        return self.registry

    def ref(self, owner, name):
        return self.refs[owner, name]

    def dictionary_stamp(self, address, max_items=None):
        return (self.generation[address], len(self.collections[address]), 0, address + 1)

    def dictionary(self, address, max_items=None, *, with_stamp=False):
        self.enumerations.append(address)
        result = list(self.collections[address])
        return (result, self.dictionary_stamp(address)) if with_stamp else result

    def reader(self):
        reader = SinglePotReader.__new__(SinglePotReader)
        reader.bound = reader.binding_witness = None
        reader.ui = SimpleNamespace(meta=self)
        reader.recipe = SimpleNamespace(context=lambda: self.context)
        reader.memory = SimpleNamespace(snapshot=lambda: {'paths': [{'trace': [
            {'pointer': 10, 'field': '_hierarchy'}, {'pointer': 20, 'field': '<entity>k__BackingField'},
        ]}]})
        reader.integer = lambda obj, field, *args: 1 if obj == 20 else 11
        reader.entity_net = lambda obj: 44 if obj == 20 else 22
        reader.state = Mock(return_value={'status': 0})
        return reader


def test_stable_ten_pot_binding_does_not_reenumerate():
    scene = Scene()
    reader = scene.reader()
    assert reader.bind(STOVE) == {'status': 0}
    assert reader.binding_metrics[0]['mode'] == 'full'
    for _ in range(28):
        assert reader.bind(STOVE) == {'status': 0}
        assert [m['mode'] for m in reader.binding_metrics] == ['quick']
        assert reader.binding_metrics[0]['seconds'] >= 0
    assert scene.enumerations == [40, 70]
    assert reader.state.call_count == 29


@pytest.mark.parametrize('changed', [40, 70])
def test_stable_collection_change_falls_back_once(changed):
    scene = Scene()
    reader = scene.reader()
    reader.bind(STOVE)
    scene.generation[changed] += 1
    reader.bind(STOVE)
    assert scene.enumerations == [40, 70, 40, 70]
    assert [(x['mode'], x['outcome']) for x in reader.binding_metrics] == [
        ('quick', 'collection_changed'), ('full', 'verified')]
    reader.bind(STOVE)
    assert scene.enumerations == [40, 70, 40, 70]


@pytest.mark.parametrize('change', ['scene', 'stove', 'owner', 'mid_read', 'state_error', 'duplicate'])
def test_unsafe_revalidation_invalidates_binding(change):
    scene = Scene()
    reader = scene.reader()
    reader.bind(STOVE)
    stove = STOVE
    if change == 'scene':
        scene.context = (1, 2, 4)
    elif change == 'stove':
        stove = (11, 22, 34)
    elif change == 'owner':
        scene.refs[50, '_hierarchyObject'] = 999
    elif change == 'mid_read':
        def mutate():
            scene.generation[40] += 1
            return {'status': 0}
        reader.state.side_effect = mutate
    elif change == 'state_error':
        reader.state.side_effect = OSError('read failed')
    else:
        scene.collections[40].append(50)
        scene.generation[40] += 1
    with pytest.raises((ValueError, OSError)):
        reader.bind(stove)
    assert reader.bound is reader.binding_witness is None
    assert reader.binding_metrics[-1]['outcome'] == 'failed'
    assert scene.enumerations == ([40, 70, 40, 70, 70] if change == 'duplicate' else [40, 70])


@pytest.mark.parametrize('address,name', [(40, 'ActiveHierarchies'), (70, '_components')])
@pytest.mark.parametrize('index,field', tuple(enumerate(('_version', '_count', '_freeCount', '_entries'))))
def test_full_binding_diagnoses_each_collection_field(address, name, index, field):
    scene = Scene()
    original = scene.dictionary_stamp
    reader = scene.reader()

    def changed(collection, max_items=None):
        stamp = list(original(collection, max_items))
        if collection == address:
            stamp[index] += 1
        return tuple(stamp)

    def finish_state():
        scene.dictionary_stamp = changed
        return {'status': 0}

    reader.state.side_effect = finish_state
    with pytest.raises(ValueError, match='完整绑定期间活动层级或组件集合发生变化'):
        reader.bind(STOVE)
    assert reader.bound is reader.binding_witness is None
    diagnostic = reader.binding_metrics[0]['collection_diagnostic']
    collection = diagnostic['collections'][name]
    before = collection['initial']['fields'][field]
    assert collection['changed_fields'] == {field: {'initial': before, 'final': before + 1}}
    assert all(c['final'] is not None for c in diagnostic['collections'].values())
    assert diagnostic['phase'] == 'compare_collection_stamps'
    assert diagnostic['match_count'] == 1


def test_full_binding_ownership_change_stops_before_stamp_reads():
    scene = Scene()
    reader = scene.reader()

    def change_owner():
        scene.refs[61, '_components'] = 71
        return {'status': 0}

    reader.state.side_effect = change_owner
    with pytest.raises(ValueError, match='已绑定灶台的活动层级或组件归属已变化'):
        reader.bind(STOVE)
    assert reader.bound is reader.binding_witness is None
    diagnostic = reader.binding_metrics[0]['collection_diagnostic']
    assert diagnostic['phase'] == 'final_ownership'
    assert diagnostic['ownership_checks'][-1] == {
        'field': '_components', 'source_address': 61, 'initial': 70, 'final': 71, 'matches': False,
    }
    assert all(c['initial'] is not None and c['final'] is None
               and c['changed_fields'] is None for c in diagnostic['collections'].values())


def test_full_binding_final_read_error_keeps_other_snapshot():
    scene = Scene()
    original = scene.dictionary_stamp
    reader = scene.reader()

    def finish_state():
        def failed(address, max_items=None):
            if address == 70:
                raise OSError('组件集合读取失败')
            return original(address, max_items)

        scene.dictionary_stamp = failed
        return {'status': 0}

    reader.state.side_effect = finish_state
    with pytest.raises(OSError, match='组件集合读取失败'):
        reader.bind(STOVE)
    assert reader.bound is reader.binding_witness is None
    record = reader.binding_metrics[0]
    diagnostic = record['collection_diagnostic']
    assert record['error_type'] == 'OSError'
    assert diagnostic['phase'] == 'final__components'
    assert diagnostic['collections']['ActiveHierarchies']['final'] is not None
    assert diagnostic['collections']['_components']['final'] is None


def test_full_binding_stable_snapshots_and_identity_are_serializable():
    import json

    reader = Scene().reader()
    assert reader.bind(STOVE) == {'status': 0}
    record = json.loads(json.dumps(reader.binding_metrics[0], allow_nan=False))
    diagnostic = record['collection_diagnostic']
    assert record['outcome'] == diagnostic['phase'] == 'verified'
    assert diagnostic['match_count'] == 1
    assert diagnostic['scene'] == {'level_node': 1, 'level': 2, 'cooking_system': 3}
    assert diagnostic['player'] == {'entity': 20, 'hierarchy': 10, 'net_id': 44}
    assert diagnostic['stove'] == {'static_id': 11, 'entity_net_id': 22, 'level_object_net_id': 33}
    assert diagnostic['matched_stove']['component'] == 80
    for collection in diagnostic['collections'].values():
        assert collection['initial']['fields'] == collection['final']['fields']
        assert collection['changed_fields'] == {}
        assert 0 <= collection['initial']['started_offset_seconds'] < collection['final']['started_offset_seconds']
        assert collection['initial']['elapsed_seconds'] >= 0
        assert collection['final']['elapsed_seconds'] >= 0
    assert reader.binding_witness is not None
