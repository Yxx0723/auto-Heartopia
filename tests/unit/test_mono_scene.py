"""Scene containers are decoded from synthetic memory only."""
import struct
import pytest
from unittest.mock import Mock
from tests.unit.test_mono_position import Runtime
from utils.drivers.mono_scene import MemorySceneMap, SceneMetadata
from utils.drivers.mono_position import MonoPositionLocator
from utils.core import thresholds as policy


def dictionary():
    r = Runtime()
    r.add_class('Entry', '', [('next', 20, 8), ('value', 32, 18)], size=40)
    array_class = r.add_class('Entry[]', '', [], size=16)
    r.write(array_class + 0x1b, b'\x05')
    r.p64(array_class, r.classes['Entry'])
    r.add_class('Dictionary`2', 'System.Collections.Generic', [('_entries', 24, 29), ('_count', 64, 8),
                ('_freeCount', 68, 8), ('_version', 76, 8)], size=80)
    array = r.make_object('Entry[]', 80)
    r.p64(array + 24, 2)
    r.write(array + 32 + 4, struct.pack('<i', -1))
    r.p64(array + 32 + 16, r.hierarchy)
    r.write(array + 56 + 4, struct.pack('<i', -2))
    r.p64(array + 56 + 16, r.character)
    obj = r.make_object('Dictionary`2', 80)
    r.p64(obj + 24, array)
    r.write(obj + 64, struct.pack('<i', 2))
    r.write(obj + 68, struct.pack('<i', 1))
    return r, obj


def test_local_hierarchy_uses_named_link_before_the_final_back_reference():
    r = Runtime()
    sample = MonoPositionLocator(r.read, r.base).snapshot()
    assert sample['paths'][0]['trace'][-1]['pointer'] != r.hierarchy
    assert MemorySceneMap.local_hierarchy(sample) == r.hierarchy


def test_entity_collision_group_uses_entity_transform_instead_of_a_missing_direct_hierarchy():
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = Mock()
    reader.meta.obj.side_effect = lambda obj: {'name': 'PhysicalCollisionGroup' if obj == 1 else 'Entity'}
    references = {(1, '<entity>k__BackingField'): 2, (2, '<transformComponent>k__BackingField'): 3, (3, '_hierarchy'): 4}
    reader.meta.ref.side_effect = lambda obj, field: references[obj, field]
    assert reader.group_hierarchy(1, {}) == 4
    assert reader.group_hierarchy(1, {1: 5}) == 5


def test_dictionary_uses_runtime_stride_and_ignores_free_entries():
    r, obj = dictionary()
    assert SceneMetadata(r.read, r.domain).dictionary(obj) == [r.hierarchy]


def test_preallocated_dictionary_capacity_is_not_treated_as_live_scene_size():
    r, obj = dictionary()
    entries = struct.unpack('<Q', r.read(obj+24, 8))[0]
    r.p64(entries+24, 36353)
    reads = []
    def read(address, size):
        reads.append((address, size))
        return r.read(address, size)
    assert SceneMetadata(read, r.domain).dictionary(obj) == [r.hierarchy]
    assert (entries+32, 48) in reads
    assert all(size < 1000 for _, size in reads)


def test_sparse_registry_can_have_more_used_slots_than_live_object_limit():
    r, obj = dictionary()
    count, stride = 20001, 24
    entries = r.make_object('Entry[]', 32+count*stride)
    r.p64(entries+24, 36353)
    data = bytearray(count*stride)
    for i in range(count):
        struct.pack_into('<i', data, i*stride+4, -2)
    struct.pack_into('<i', data, (count-1)*stride+4, -1)
    struct.pack_into('<Q', data, (count-1)*stride+16, r.hierarchy)
    r.write(entries+32, data)
    r.p64(obj+24, entries)
    r.write(obj+64, struct.pack('<ii', count, count-1))
    assert SceneMetadata(r.read, r.domain).dictionary(obj) == [r.hierarchy]


@pytest.mark.parametrize('fault', ['capacity', 'live_count', 'free_count', 'slot_count', 'bytes'])
def test_large_container_support_keeps_capacity_live_count_and_byte_bounds(fault):
    r, obj = dictionary()
    entries = struct.unpack('<Q', r.read(obj+24, 8))[0]
    if fault == 'capacity':
        r.p64(entries+24, policy.COORD_SCENE_MAX_CAPACITY+1)
    elif fault == 'live_count':
        r.write(obj+64, struct.pack('<ii', 20001, 0))
    elif fault == 'free_count':
        r.write(obj+68, struct.pack('<i', 3))
    elif fault == 'slot_count':
        r.write(obj+68, b'\0'*4)
    else:
        count = 200000
        r.p64(entries+24, count)
        r.write(obj+64, struct.pack('<ii', count, count-1))
    with pytest.raises(ValueError):
        SceneMetadata(r.read, r.domain).dictionary(obj)


@pytest.mark.parametrize('field', ['_count', '_freeCount'])
def test_removal_without_version_increment_is_rejected(field):
    r, obj = dictionary()
    entries = struct.unpack('<Q', r.read(obj+24, 8))[0]
    offset = 64 if field == '_count' else 68
    def read(address, size):
        raw = r.read(address, size)
        if address == entries+32:
            r.write(obj+offset, b'\0'*4)
        return raw
    with pytest.raises(ValueError, match='变化'):
        SceneMetadata(read, r.domain).dictionary(obj)


def test_direct_reference_array_still_obeys_live_item_limit():
    r, obj = dictionary()
    entries = struct.unpack('<Q', r.read(obj+24, 8))[0]
    r.p64(entries+24, 36353)
    with pytest.raises(ValueError, match='过大'):
        SceneMetadata(r.read, r.domain).array_refs(entries)


def test_empty_dictionary_does_not_require_an_allocated_entries_array():
    r, obj = dictionary()
    r.p64(obj + 24, 0)
    r.write(obj + 64, b'\0' * 4)
    r.write(obj + 68, b'\0' * 4)
    assert SceneMetadata(r.read, r.domain).dictionary(obj) == []


@pytest.mark.parametrize('problem', ['count', 'domain', 'version'])
def test_invalid_or_changing_container_is_rejected(problem):
    r, obj = dictionary()
    if problem == 'count':
        r.write(obj + 64, struct.pack('<i', 100000000))
    if problem == 'domain':
        r.p64(r.slots['Dictionary`2'] + 16, 0)
    def read(address, size):
        data = r.read(address, size)
        if problem == 'version' and address == obj + 64:
            r.write(obj + 76, struct.pack('<i', 1))
        return data
    with pytest.raises(ValueError):
        SceneMetadata(read, r.domain).dictionary(obj)


def shrub_runtime():
    r, components = dictionary()
    r.add_class('Entity', 'XDTGame.GAS.Client', [('_components', 72, 21)], size=160)
    r.add_class('GatherComponent', 'XDTGame.GAS.Client', [('<entity>k__BackingField', 16, 18)], size=48)
    r.add_class('CollectableBushComponent', 'XDTLevelAndEntity.Gameplay.Component.Gather',
                [('_itemTypeID', 52, 8)],
                parent='GatherComponent', size=56)
    entity = r.make_object('Entity', 160)
    owner = r.make_object('LevelEntityComponent', 336)
    component = r.make_object('CollectableBushComponent', 56)
    r.p64(owner + 16, entity)
    r.p64(entity + 72, components)
    r.p64(component + 16, entity)
    entries = struct.unpack('<Q', r.read(components + 24, 8))[0]
    r.p64(entries + 48, component)
    scene = MemorySceneMap.__new__(MemorySceneMap)
    scene.meta = SceneMetadata(r.read, r.domain)
    return r, scene, owner, entity, component, components


def test_harvestable_bush_uses_exact_component_identity_and_entity_back_reference():
    r, scene, owner, _, _, _ = shrub_runtime()
    assert scene.object_category(owner, scene.meta.obj(owner)) == 'shrub'


@pytest.mark.parametrize('item_id, expected', [(101, 'bush'), (102, 'blueberry'), (103, 'raspberry'),
                                              (0, 'shrub'), (104, 'shrub'), (-1, 'shrub')])
def test_verified_item_identity_separates_berry_species_without_guessing_unknown_bushes(item_id, expected):
    r, scene, owner, _, component, _ = shrub_runtime()
    r.write(component + 52, struct.pack('<i', item_id))
    assert scene.object_category(owner, scene.meta.obj(owner)) == expected


def test_changing_item_identity_does_not_label_a_bush_with_an_outdated_species():
    r, scene, owner, _, component, _ = shrub_runtime()
    r.write(component + 52, struct.pack('<i', 102))
    def read(address, size):
        value = r.read(address, size)
        if address == component + 52:
            r.write(component + 52, struct.pack('<i', 103))
        return value
    scene.meta = SceneMetadata(read, r.domain)
    assert scene.object_category(owner, scene.meta.obj(owner)) == 'other'


@pytest.mark.parametrize('problem', [
    'ordinary_component', 'similar_name', 'component_namespace', 'entity_namespace',
    'dictionary_namespace', 'missing_components', 'wrong_component_owner',
    'too_many_components', 'invalid_count_type', 'component_domain',
])
def test_unknown_invalid_or_ambiguous_objects_remain_generic(problem):
    r, scene, owner, entity, component, components = shrub_runtime()
    if problem in ('ordinary_component', 'similar_name'):
        name = 'CollectableRockComponent' if problem == 'ordinary_component' else 'CollectableBushComponentProxy'
        r.p64(r.classes['CollectableBushComponent'] + 0x48, r.string(name))
    elif problem == 'component_namespace':
        r.p64(r.classes['CollectableBushComponent'] + 0x50, r.string('Unrelated.Component.Gather'))
    elif problem == 'entity_namespace':
        r.p64(r.classes['Entity'] + 0x50, r.string('Unrelated.Entity'))
    elif problem == 'dictionary_namespace':
        r.p64(r.classes['Dictionary`2'] + 0x50, r.string('Unrelated.Collections'))
    elif problem == 'missing_components':
        r.p64(entity + 72, 0)
    elif problem == 'wrong_component_owner':
        r.p64(component + 16, r.entity)
    elif problem == 'too_many_components':
        r.write(components + 64, struct.pack('<i', policy.COORD_SCENE_ENTITY_COMPONENT_LIMIT + 1))
    elif problem == 'invalid_count_type':
        table = struct.unpack('<Q', r.read(r.classes['Dictionary`2'] + 0xa0, 8))[0]
        field_type = struct.unpack('<Q', r.read(table + 32, 8))[0]
        r.write(field_type + 10, b'\x0c')
    elif problem == 'component_domain':
        r.p64(r.slots['CollectableBushComponent'] + 16, 0)
    assert scene.object_category(owner, scene.meta.obj(owner)) == 'other'


@pytest.mark.parametrize('change', ['version', 'components', 'entity'])
def test_component_changes_during_classification_cannot_yield_a_bush(change):
    r, scene, owner, entity, component, components = shrub_runtime()
    def read(address, size):
        value = r.read(address, size)
        if address == component:
            if change == 'version':
                r.write(components + 76, struct.pack('<i', 1))
            elif change == 'components':
                r.p64(entity + 72, 0)
            else:
                r.p64(owner + 16, r.entity)
        return value
    scene.meta = SceneMetadata(read, r.domain)
    assert scene.object_category(owner, scene.meta.obj(owner)) == 'other'


def test_static_decoration_is_not_classified_from_position_or_a_bush_like_name():
    scene = MemorySceneMap.__new__(MemorySceneMap)
    scene.meta = Mock()
    assert scene.object_category(123, {'name': 'StaticLevelObjectWrap', 'namespace': ''}) == 'other'
    assert scene.object_category(123, {'name': 'Bush', 'namespace': ''}) == 'other'
    scene.meta.obj.assert_not_called()


@pytest.mark.parametrize('name', ['CollectableTreeComponent', 'CollectableAdvancedTreeComponent'])
@pytest.mark.parametrize('problem', [None, 'namespace', 'similar_name', 'owner', 'changing_owner', 'dictionary'])
def test_tree_identity_requires_exact_component_and_stable_entity_membership(name, problem):
    r, scene, owner, entity, _, components = shrub_runtime()
    r.add_class(name, 'XDTLevelAndEntity.Gameplay.Component.Gather', [], parent='GatherComponent', size=48)
    component = r.make_object(name, 48)
    r.p64(component + 16, entity)
    entries = struct.unpack('<Q', r.read(components + 24, 8))[0]
    r.p64(entries + 48, component)
    if problem == 'namespace':
        r.p64(r.classes[name] + 0x50, r.string('Unrelated.Gather'))
    elif problem == 'similar_name':
        r.p64(r.classes[name] + 0x48, r.string(name + 'Proxy'))
    elif problem == 'owner':
        r.p64(component + 16, r.entity)
    reads = 0
    def read(address, size):
        nonlocal reads
        value = r.read(address, size)
        if address == component + 16:
            reads += 1
            if problem == 'changing_owner' and reads == 1:
                r.p64(component + 16, r.entity)
        if problem == 'dictionary' and address == component:
            r.write(components + 76, struct.pack('<i', 1))
        return value
    scene.meta = SceneMetadata(read, r.domain)
    assert scene.object_category(owner, scene.meta.obj(owner)) == ('other' if problem or name == 'CollectableAdvancedTreeComponent' else 'tree')


def test_conflicting_tree_and_bush_components_are_not_classified_as_either_plant():
    r, scene, owner, entity, _, components = shrub_runtime()
    r.add_class('CollectableTreeComponent', 'XDTLevelAndEntity.Gameplay.Component.Gather', [],
                parent='GatherComponent', size=48)
    component = r.make_object('CollectableTreeComponent', 48)
    r.p64(component + 16, entity)
    entries = struct.unpack('<Q', r.read(components + 24, 8))[0]
    r.write(entries + 56 + 4, struct.pack('<i', -1))
    r.p64(entries + 56 + 16, component)
    assert scene.object_category(owner, scene.meta.obj(owner)) == 'other'


def insect_runtime(name='钩粉蝶'):
    r, scene, owner, entity, _, components = shrub_runtime()
    r.add_class('String', 'System', [], size=24)
    r.add_class('TableInsect', '', [('name', 16, 14), ('id', 24, 8)], size=32)
    r.add_class('InsectComponentData', 'XDTDataAndProtocol.ComponentsData', [('staticId', 16, 8)], size=32)
    r.add_class('InsectComponent', 'XDTLevelAndEntity.Gameplay.Component.Insect', [
        ('_tableInsect', 48, 18), ('_levelEntityComponent', 56, 18), ('_componentData', 64, 17)],
        parent='GatherComponent', size=80)
    field_table = struct.unpack('<Q', r.read(r.classes['InsectComponent'] + 0xa0, 8))[0]
    data_type = struct.unpack('<Q', r.read(field_table + 64, 8))[0]
    r.p64(data_type, r.classes['InsectComponentData'])
    raw = name.encode('utf-16le')
    text = r.make_object('String', 22 + len(raw))
    r.write(text + 16, struct.pack('<i', len(raw) // 2))
    r.write(text + 20, raw)
    table = r.make_object('TableInsect', 32)
    r.p64(table + 16, text)
    r.write(table + 24, struct.pack('<i', 51107))
    component = r.make_object('InsectComponent', 80)
    r.p64(component + 16, entity)
    r.p64(component + 48, table)
    r.p64(component + 56, owner)
    r.write(component + 64, struct.pack('<i', 51107))
    entries = struct.unpack('<Q', r.read(components + 24, 8))[0]
    r.p64(entries + 48, component)
    return r, scene, owner, entity, component, table, text, components


@pytest.mark.parametrize('name,category', [('钩粉蝶', 'butterfly'), ('橙粉蝶', 'butterfly'),
                                          ('Common blue butterfly', 'butterfly'), ('绿虎甲', 'insect'),
                                          ('七星瓢虫', 'insect'), ('白尾蜻蜓', 'insect'), ('', 'insect')])
def test_insect_configuration_name_separates_butterflies_from_other_flying_insects(name, category):
    _, scene, owner, _, _, _, _, _ = insect_runtime(name)
    assert scene.object_category(owner, scene.meta.obj(owner)) == category


@pytest.mark.parametrize('problem,expected', [('table_id', 'insect'), ('table_namespace', 'insect'),
                                            ('name_length', 'insect'), ('name_type', 'insect'),
                                            ('wrong_owner', 'other'), ('wrong_entity', 'other'),
                                            ('component_namespace', 'other')])
def test_invalid_insect_links_never_create_a_false_butterfly(problem, expected):
    r, scene, owner, _, component, table, text, _ = insect_runtime()
    if problem == 'table_id':
        r.write(table + 24, struct.pack('<i', 51201))
    elif problem == 'table_namespace':
        r.p64(r.classes['TableInsect'] + 0x50, r.string('Unrelated.Config'))
    elif problem == 'name_length':
        r.write(text + 16, struct.pack('<i', policy.COORD_SCENE_LABEL_MAX_CHARS + 1))
    elif problem == 'name_type':
        r.p64(table + 16, r.character)
    elif problem == 'wrong_owner':
        r.p64(component + 56, r.transform)
    elif problem == 'wrong_entity':
        r.p64(component + 16, r.entity)
    else:
        r.p64(r.classes['InsectComponent'] + 0x50, r.string('Unrelated.Insect'))
    assert scene.object_category(owner, scene.meta.obj(owner)) == expected


@pytest.mark.parametrize('change', ['table', 'id', 'name_reference', 'dictionary'])
def test_insect_configuration_changes_cannot_produce_a_stale_butterfly(change):
    r, scene, owner, _, component, table, text, components = insect_runtime()
    def read(address, size):
        value = r.read(address, size)
        if address == text + 20:
            if change == 'table':
                r.p64(component + 48, 0)
            elif change == 'id':
                r.write(component + 64, struct.pack('<i', 51201))
            elif change == 'name_reference':
                r.p64(table + 16, 0)
            else:
                r.write(components + 76, struct.pack('<i', 1))
        return value
    scene.meta = SceneMetadata(read, r.domain)
    assert scene.object_category(owner, scene.meta.obj(owner)) == 'other'


@pytest.mark.parametrize('name,namespace,expected', [
    ('BirdComponent', 'ScriptsRefactory.LevelAndEntity.Entity.Components', 'bird'),
    ('AnimalMovementComponent', 'XDTLevelAndEntity.Gameplay.Component.Animal', 'animal'),
    ('BirdScannableComponent', 'ScriptsRefactory.LevelAndEntity.Entity.Components', 'other'),
    ('PlayerAnimalInteractionComponent', 'XDTLevelAndEntity.Gameplay.Component.Player', 'other'),
    ('AnimalMovementComponent', 'Unrelated.Animal', 'other'),
])
def test_birds_and_animals_require_exact_components_owned_by_the_entity(name, namespace, expected):
    r, scene, owner, entity, _, components = shrub_runtime()
    r.add_class(name, namespace, [], parent='GatherComponent', size=48)
    component = r.make_object(name, 48)
    r.p64(component + 16, entity)
    entries = struct.unpack('<Q', r.read(components + 24, 8))[0]
    r.p64(entries + 48, component)
    assert scene.object_category(owner, scene.meta.obj(owner)) == expected
    r.p64(component + 16, r.entity)
    assert scene.object_category(owner, scene.meta.obj(owner)) == 'other'
