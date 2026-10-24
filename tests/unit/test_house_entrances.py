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


def public_runtime():
    r,reader,old_rows=runtime();m=reader.meta
    ns='EcsClient.XDT.Scene.Shared.Data.ServerData'
    old_config=r.parts.config;wrapper=m.ref(old_config,'_behaviour');assets=m.ref(old_config,'MapFieldPointsAsset')
    r.add_class('MainGameLevelConfig','XDTDataAndProtocol.Config.GameLevel',
        [('_behaviour',16,18),('MapFieldPointsAsset',24,18),('TriggerAssets',32,18),('MapCommonAssets',40,18)],size=48)
    config=r.make_object('MainGameLevelConfig',48);r.parts.config=config
    r.p64(config+16,wrapper);r.p64(config+24,assets);r.p64(wrapper+16,config)
    r.add_class('MapFieldPointAsset',NAMESPACE,
        [('fieldID',16,8),('fieldNo',20,8),('pos',24,17),('playerPos',36,17),('foreyardAreaId',48,8)],size=56)
    house_array=m.ref(r.parts.points,'_items')
    for n in range(12):
        row=r.make_object('MapFieldPointAsset',56)
        r.write(row+16,struct.pack('<ii6fi',n+1,n+1,0.,20.,n*20.,9.,20.,n*20.,100+n))
        r.p64(house_array+32+n*8,row)
    r.add_class('TriggerAssets',ns,[('triggers',16,18)],size=24)
    r.add_class('TriggerAsset',ns,[('triggerId',16,8),('polygon',24,17)],size=40)
    r.add_class('MapCommonAssets',NAMESPACE,[('mapRectMin',16,17),('mapSize',24,17)],size=32)
    r.add_class('Area','',[('points',16,29),('area',24,12),('areaPercent',28,12)],size=32)
    r.add_class('Vector2','UnityEngine',[('x',16,12),('y',20,12)],size=24)
    def type_field(klass,index,typ):
        fields=struct.unpack('<Q',r.read(r.classes[klass]+0xa0,8))[0]
        at=struct.unpack('<Q',r.read(fields+index*32,8))[0];r.p64(at,r.classes[typ])
    type_field('TriggerAsset',1,'Area')
    type_field('MapCommonAssets',0,'Vector2');type_field('MapCommonAssets',1,'Vector2')
    for array,element in (('Trigger[]','TriggerAsset'),('Vector3[]','Vector3')):
        r.add_class(array,'',[],size=16);r.write(r.classes[array]+0x1b,b'\x05');r.p64(r.classes[array],r.classes[element])
    trigger_assets=r.make_object('TriggerAssets',24);collection=r.make_object('List`1',32)
    refs=r.make_object('Trigger[]',32+12*8);r.p64(refs+24,12)
    r.p64(collection+16,refs);r.write(collection+24,struct.pack('<ii',12,1));r.p64(trigger_assets+16,collection)
    triggers=[];polygons=[]
    for n in range(12):
        row=r.make_object('TriggerAsset',40);points=r.make_object('Vector3[]',32+4*12)
        r.p64(points+24,4);r.write(points+32,struct.pack('<12f',4.,20.,n*20.-8.,12.,20.,n*20.-8.,12.,20.,n*20.+8.,4.,20.,n*20.+8.))
        r.write(row+16,struct.pack('<i',100+n));r.p64(row+24,points);r.p64(refs+32+n*8,row)
        triggers.append(row);polygons.append(points)
    common=r.make_object('MapCommonAssets',32);r.write(common+16,struct.pack('<4f',-500.,-500.,1000.,1000.))
    r.p64(config+32,trigger_assets);r.p64(config+40,common)
    return r,reader,collection,triggers,polygons


def test_public_policy_uses_matching_forecourt_geometry_and_caches_current_snapshot():
    r,reader,_,_,_=public_runtime()
    houses,polygons=reader.public_snapshot()
    assert len(houses)==len(polygons)==12
    assert all(h['public_position'][0]>9. and h['public_source']=='forecourt-public-side-policy' for h in houses)
    assert reader.public_snapshot()[0] is houses
    r.p64(r.parts.node+16,0)
    with pytest.raises(ValueError):reader.public_snapshot()


@pytest.mark.parametrize('fault',['missing','duplicate','nan','wrong_element'])
def test_public_guard_rejects_incomplete_changed_or_mistyped_trigger_geometry(fault):
    r,reader,collection,rows,polygons=public_runtime();reader.public_snapshot()
    if fault=='missing':r.write(collection+24,struct.pack('<i',11))
    if fault=='duplicate':r.write(rows[-1]+16,struct.pack('<i',100))
    if fault=='nan':r.write(polygons[-1]+32,struct.pack('<f',float('nan')))
    if fault=='wrong_element':
        r.add_class('WrongPoints[]','',[],size=16)
        r.write(r.classes['WrongPoints[]']+0x1b,b'\x05');r.p64(r.classes['WrongPoints[]'],r.classes['MapFieldPointAsset'])
        wrong=r.make_object('WrongPoints[]',32+48);r.p64(wrong+24,4);r.p64(rows[-1]+24,wrong)
    r.write(collection+28,struct.pack('<i',2))
    with pytest.raises(ValueError):reader.public_snapshot()
