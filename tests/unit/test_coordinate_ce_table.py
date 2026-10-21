import xml.etree.ElementTree as ET

import pytest

from utils.core.coordinate_ce_table import current_position_table


def test_export_contains_only_three_readable_float_addresses_for_the_current_session():
    xml = current_position_table({'state': 'connected', 'pid': 17836,
        'position': [98., 21.5, -9.], 'address': 0x123450, 'sample_age': 0.})
    root = ET.fromstring(xml)
    entries = root.findall('./CheatEntries/CheatEntry')
    assert [entry.findtext('Address') for entry in entries] == ['123450', '123454', '123458']
    assert [entry.findtext('VariableType') for entry in entries] == ['Float'] * 3
    assert '17836' in root.findtext('Comments')
    assert '重启' in root.findtext('Comments')
    assert root.find('LuaScript') is None
    assert root.findall('.//AssemblerScript') == []


@pytest.mark.parametrize('extra', [
    {'state': 'disconnected'}, {'position': None}, {'position': [float('nan'), 1, 2]},
    {'address': 0}, {'address': None}, {'sample_age': 100},
])
def test_unavailable_or_stale_coordinates_cannot_be_exported(extra):
    status = {'state': 'connected', 'pid': 17836, 'position': [1., 2., 3.],
              'address': 0x123450, 'sample_age': 0., **extra}
    with pytest.raises(ValueError):
        current_position_table(status)


def test_computed_attached_world_position_never_exports_the_local_address():
    with pytest.raises(ValueError, match='下车'):
        current_position_table({'state': 'connected', 'position': [100., 20., 50.],
                                'address': 0x123450, 'position_computed': True})
