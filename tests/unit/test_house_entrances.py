"""Current-world typed door positions, never plot IDs or resource centres."""
import struct

import pytest

from tests.unit.test_global_minerals import definition_runtime
from utils.drivers.house_entrances import HouseEntranceReader, NAMESPACE


def runtime():
    r, original, _ = definition_runtime()
    fields = struct.unpack('<Q', r.read(r.classes['MainGameLevelConfig']+0xa0, 8))[0]
    r.p64(fields+32+8, r.string('MapFieldPointsAsset'))
    r.add_class('MapFieldPointsAsset', NAMESPACE, [('fieldPoints', 16, 18)], size=24)
    r.add_class('MapFieldPointAsset', NAMESPACE,
                [('fieldID', 16, 8), ('fieldNo', 20, 8), ('pos', 24, 17), ('playerPos', 36, 17)], size=48)
    r.add_class('HousePoint[]', '', [], size=16)
    r.write(r.classes['HousePoint[]']+0x1b, b'\x05')
    r.p64(r.classes['HousePoint[]'], r.classes['MapFieldPointAsset'])
    assets = r.make_object('MapFieldPointsAsset', 24)
    array = r.make_object('HousePoint[]', 32+12*8)
    r.p64(array+24, 12)
    rows = []
    for i in range(12):
        row = r.make_object('MapFieldPointAsset', 48)
        # field IDs intentionally reverse the displayed numbers.
        r.write(row+16, struct.pack('<ii6f', 12-i, i+1, i*20., 20., 0., i*20.+9., 20., 0.))
        r.p64(array+32+i*8, row)
        rows.append(row)
    r.p64(r.parts.config+24, assets)
    r.p64(assets+16, r.parts.points)
    r.p64(r.parts.points+16, array)
    r.write(r.parts.points+24, struct.pack('<ii', 12, 1))
    reader = HouseEntranceReader(original.meta, r.base, {}, classes=(r.classes['GameWorld'], r.classes['ScriptEngine']))
    return r, reader, rows


def test_all_twelve_player_positions_in_number_order_and_cache():
    _, reader, _ = runtime()
    result = reader.snapshot()
    assert [p['house_number'] for p in result] == list(range(1, 13))
    assert result[0]['field_id'] == 12 and result[0]['position'] == [9., 20., 0.]
    assert reader.snapshot() is result and reader.loads == 1


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'nan', 'world', 'unregistered'])
def test_invalid_or_foreign_config_never_supplies_cached_doors(fault):
    r, reader, rows = runtime()
    reader.snapshot()
    if fault == 'missing': r.write(r.parts.points+24, struct.pack('<i', 11))
    if fault == 'duplicate': r.write(rows[-1]+20, struct.pack('<i', 1))
    if fault == 'nan': r.write(rows[-1]+36, struct.pack('<f', float('nan')))
    if fault in ('duplicate', 'nan'): r.write(r.parts.points+28, struct.pack('<i', 2))
    if fault == 'world': r.p64(r.parts.node+16, 0)
    if fault == 'unregistered':
        r.write(r.parts.entries+36, struct.pack('<i', -2))
        r.write(r.parts.registry+68, struct.pack('<i', 2))
    with pytest.raises(ValueError): reader.snapshot()
