"""Real metadata decoding over synthetic memory; never sends game input."""
import struct
from types import SimpleNamespace

import pytest

from tests.unit.test_mono_scene import dictionary
from utils.drivers.mono_scene import MemorySceneMap, SceneMetadata

BAKE = 'XDTLevelAndEntity.BaseSystem.RenderingManager.BakeRendering'


def runtime(processor_name='BakeRenderingProcessorProcessorForFence', namespace=BAKE):
    r, colliders = dictionary()
    r.add_class('List`1', 'System.Collections.Generic',
                [('_items', 16, 29), ('_size', 24, 8), ('_version', 28, 8)], size=32)
    r.add_class('RenderingSystem', 'XDTLevelAndEntity.BaseSystem.RenderingManager',
                [('<Instance>k__BackingField', 0, 18, 17), ('<LoaderManager>k__BackingField', 16, 18)], size=24)
    r.add_class('LoaderManager', 'XDTLevelAndEntity.BaseSystem.LoadManager', [('_bakeCollections', 16, 21)], size=24)
    r.add_class('NetBakeCollection', BAKE, [('_gridFusionBakes', 16, 21)], size=24)
    r.add_class(processor_name, namespace, [('bakeCollection', 16, 18), ('_colliderObjs', 24, 21)], size=32)
    r.add_class('RenderPhysicTarget', BAKE + '.Collider', [('_hierarchy', 16, 18), ('_physicObjects', 24, 21)], size=32)

    def make_list(element, refs, capacity=None):
        array_name = element + '[]'
        cls = r.add_class(array_name, '', [], size=16)
        r.write(cls + 0x1b, b'\x05')
        r.p64(cls, r.classes[element])
        cap = len(refs) if capacity is None else capacity
        array = r.make_object(array_name, 32 + cap * 8)
        r.p64(array + 24, cap)
        for i, ref in enumerate(refs):
            r.p64(array + 32 + i * 8, ref)
        obj = r.make_object('List`1', 32)
        r.p64(obj + 16, array)
        r.write(obj + 24, struct.pack('<i', len(refs)))
        return obj, array

    root = r.make_object('RenderingSystem', 24)
    loader = r.make_object('LoaderManager', 24)
    collection = r.make_object('NetBakeCollection', 24)
    processor = r.make_object(processor_name, 32)
    target = r.make_object('RenderPhysicTarget', 32)
    collections, _ = make_list('NetBakeCollection', [collection])
    processors, _ = make_list(processor_name, [processor])
    targets, array = make_list('RenderPhysicTarget', [target], capacity=3)
    # Garbage in unused slots must not be mistaken for another fence target.
    r.p64(array + 40, r.entity)
    r.p64(root + 16, loader)
    r.p64(loader + 16, collections)
    r.p64(collection + 16, processors)
    r.p64(processor + 16, collection)
    r.p64(processor + 24, targets)
    r.p64(target + 16, r.hierarchy)
    r.p64(target + 24, colliders)
    storage = r.alloc(8)
    r.p64(storage, root)
    r.p64(r.slots['RenderingSystem'] + 0x70, storage)
    image_classes = [cls for cls in r.classes.values() if r.read(cls + 0x1b, 1) == b'\x01']
    buckets = r.alloc(len(image_classes) * 8)
    for i, cls in enumerate(image_classes):
        r.p64(buckets + i * 8, cls)
    r.write(r.image + 0x4e8, struct.pack('<IIQ', len(image_classes), len(image_classes), buckets))
    reader = MemorySceneMap.__new__(MemorySceneMap)
    reader.meta = SceneMetadata(r.read, r.domain)
    reader.memory = SimpleNamespace(locator=SimpleNamespace(character_class=r.classes['Character']))
    return r, reader, target, processor, collection, targets, colliders, make_list


@pytest.mark.parametrize('name,expected', [
    ('BakeRenderingProcessorProcessorForFence', 'fence'),
    ('BakeRenderingProcessorProcessorForGridFence', 'fence'),
    ('BakeRenderingProcessorProcessorForStairHandrail', 'handrail'),
    ('BakeRenderingProcessorProcessorForFenceProxy', None),
    ('BakeRenderingProcessorProcessorForWallConnectedAccess', None),
])
def test_only_exact_processors_classify_their_own_collision_targets(name, expected):
    r, reader, target, *_ = runtime(name)
    owners = reader.barrier_owners()
    assert reader.collider_barrier(r.hierarchy, target, r.hierarchy, owners) == expected
    if expected:
        assert set(owners) == {target}
        assert owners[target]['colliders'] == {r.hierarchy}


def test_identically_named_processor_from_another_namespace_stays_unclassified():
    _, reader, *_ = runtime(namespace='Unrelated.Rendering')
    assert reader.barrier_owners() == {}


@pytest.mark.parametrize('problem', ['collection', 'target_namespace', 'target_domain', 'dictionary_namespace'])
def test_invalid_or_unowned_runtime_targets_are_rejected(problem):
    r, reader, target, processor, _, _, _, _ = runtime()
    if problem == 'collection':
        r.p64(processor + 16, r.entity)
    elif problem == 'target_namespace':
        r.p64(r.classes['RenderPhysicTarget'] + 0x50, r.string('Unrelated.Collider'))
    elif problem == 'target_domain':
        r.p64(r.slots['RenderPhysicTarget'] + 16, 0)
    else:
        r.p64(r.classes['Dictionary`2'] + 0x50, r.string('Unrelated.Collections'))
    with pytest.raises(ValueError):
        reader.barrier_owners()


@pytest.mark.parametrize('change', ['hierarchy', 'dictionary', 'version', 'membership'])
def test_recycled_or_changed_target_cannot_label_a_different_solid(change):
    r, reader, target, _, _, _, dictionary, _ = runtime()
    owners = reader.barrier_owners()
    collider = r.hierarchy
    if change == 'hierarchy':
        r.p64(target + 16, r.entity)
    elif change == 'dictionary':
        r.p64(target + 24, 0)
    elif change == 'version':
        r.write(dictionary + 76, struct.pack('<i', 1))
    else:
        collider = r.entity
    assert reader.collider_barrier(collider, target, r.hierarchy, owners) is None


@pytest.mark.parametrize('field', ['_size', '_version', '_items'])
def test_list_mutations_are_rejected(field):
    r, reader, target, _, _, targets, _, _ = runtime()
    offset = reader.meta.field(r.classes['List`1'], field)['offset']
    def read(address, size):
        value = r.read(address, size)
        if address == target:
            r.write(targets + offset, struct.pack('<Q' if field == '_items' else '<i', 0 if field == '_items' else 2))
        return value
    reader.meta = SceneMetadata(read, r.domain)
    with pytest.raises(ValueError):
        reader.barrier_owners()


def test_value_arrays_are_not_read_as_pointer_lists():
    r, reader, _, _, _, _, _, make_list = runtime()
    r.add_class('ValueType', 'System', [], size=16)
    r.add_class('ColliderStruct', '', [], parent='ValueType', size=24)
    refs, _ = make_list('ColliderStruct', [r.entity])
    with pytest.raises(ValueError):
        reader.meta.list_refs(refs)


def test_current_image_lookup_rejects_a_class_cache_cycle():
    r, reader, *_ = runtime()
    r.p64(r.classes['Character'] + 0x108, r.classes['Character'])
    with pytest.raises(ValueError):
        reader.barrier_owners()
