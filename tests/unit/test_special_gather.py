"""Advanced resource identities must come from coherent live objects."""
import struct
from unittest.mock import Mock
import pytest
from utils.drivers.special_gather import special_gather, NAMESPACE


def runtime(kind='Stone'):
    meta = Mock()
    infos = {
        100: {'address': 1, 'size': 128, 'name': 'CollectableAdvanced'+kind+'Component', 'namespace': NAMESPACE},
        200: {'address': 2, 'size': 40, 'name': 'TableAdvcollectable', 'namespace': ''},
        300: {'address': 3, 'size': 96, 'name': 'CollectableObjectComponent', 'namespace': NAMESPACE}}
    classes = {10: {'address': 10, 'size': 20, 'name': 'Advanced'+kind+'ComponentData',
                    'namespace': 'ScriptsRefactory.DataAndProtocol.ComponentsData'},
               20: {'address': 20, 'size': 40, 'name': 'CollectableObjectData',
                    'namespace': 'ScriptsRefactory.DataAndProtocol.ComponentsData'}}
    def field(offset, code, typ=0): return dict(offset=offset, code=code, flags=6, type=typ)
    fields = {(1, '_componentData'): field(104, 17, 10), (10, 'staticId'): field(16, 8),
              (2, 'id'): field(24, 8), (3, '_componentData'): field(48, 17, 20),
              (3, 'itemTypeID'): field(80, 8),
              (20, 'availableNum'): field(28, 8), (20, 'coldEndTime'): field(16, 10)}
    refs = {(100, '<entity>k__BackingField'): 400, (100, '_tableConfig'): 200,
            (100, '_collectable'): 300, (300, '<entity>k__BackingField'): 400, (300, '_proxy'): 100}
    raw = struct.pack('<qfi8x', 0, 0., 3)
    meta.obj.side_effect = infos.__getitem__
    meta.cls.side_effect = classes.__getitem__
    meta.field.side_effect = lambda ci, name: fields[ci, name]
    meta.ref.side_effect = lambda obj, name: refs[obj, name]
    meta.integer.side_effect = lambda at: (402 if kind == 'Stone' else 401) if at == 380 else (42002 if kind == 'Stone' else 42001)
    meta.data.return_value = raw
    return meta, infos, fields, refs


@pytest.mark.parametrize('kind,category', [('Stone', 'fluorite'), ('Tree', 'roaming_oak')])
def test_exact_resource_identity_and_owned_remaining_count(kind, category):
    meta, *_ = runtime(kind)
    result = special_gather(meta, 100, 400)
    assert result['category'] == category and result['resource_available'] == 3
    assert result['map_dynamic'] is True and 'map_static' not in result


@pytest.mark.parametrize('fault', ['foreign_owner', 'foreign_proxy', 'wrong_table', 'changed_count', 'static_field', 'bad_id'])
def test_uncertain_resources_are_never_action_targets(fault):
    meta, infos, fields, refs = runtime()
    if fault == 'foreign_owner': refs[100, '<entity>k__BackingField'] = 401
    if fault == 'foreign_proxy': refs[300, '_proxy'] = 99
    if fault == 'wrong_table': infos[200]['name'] = 'UnrelatedTable'
    if fault == 'changed_count': meta.data.side_effect = [struct.pack('<qfi8x', 0, 0., 3), struct.pack('<qfi8x', 0, 0., 2)]
    if fault == 'static_field': fields[20, 'availableNum']['flags'] |= 16
    if fault == 'bad_id': meta.integer.side_effect = lambda at: 0
    with pytest.raises(ValueError): special_gather(meta, 100, 400)


def test_similar_component_name_is_not_a_special_resource():
    meta, infos, *_ = runtime()
    infos[100]['name'] += 'Proxy'
    assert special_gather(meta, 100, 400) is None
