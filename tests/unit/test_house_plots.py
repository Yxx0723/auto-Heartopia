"""Build-field boundaries: identity, transforms, invalidation and fail-closed reads."""
import math
import struct
from unittest.mock import Mock

import pytest

from tests.unit.test_house_entrances import runtime
from utils.drivers.house_plots import HousePlotReader, plot_geometry


def plot_runtime():
    r, entrances, old = runtime()
    ns = 'EcsClient.XDT.Scene.Shared.Data.ServerData.ResourceSpawnConfig'
    r.add_class('Quaternion', 'UnityEngine', [('x',16,12), ('y',20,12), ('z',24,12), ('w',28,12)], size=32)
    r.add_class('MapFieldPointAsset', ns,
        [('fieldID',16,8), ('fieldNo',20,8), ('pos',24,17), ('rot',36,17), ('playerPos',52,17)], size=64)
    fields = struct.unpack('<Q', r.read(r.classes['MapFieldPointAsset']+0xa0,8))[0]
    typ = struct.unpack('<Q', r.read(fields+3*32,8))[0]
    r.p64(typ, r.classes['Quaternion'])
    table = entrances.meta.ref(r.parts.points, '_items')
    rows = []
    for i, previous in enumerate(old):
        row = r.make_object('MapFieldPointAsset', 64)
        r.write(row+16, r.read(previous+16,20))
        r.write(row+36, struct.pack('<4f',0.,0.,0.,1.))
        r.write(row+52, r.read(previous+36,12))
        r.p64(table+32+i*8,row)
        rows.append(row)
    reader = HousePlotReader(entrances)
    reader.limits = Mock(return_value=((r.domain,1,2),3,(12.,12.,9.,21.)))
    return r,reader,rows


def test_twelve_unique_numbers_preserve_field_id_not_sorted_id():
    _,reader,_ = plot_runtime()
    rows=reader.snapshot()
    assert [r['house_number'] for r in rows] == list(range(1,13))
    assert [r['field_id'] for r in rows] == list(range(12,0,-1))
    assert rows[0]['polygon'] == [[-12.,-21.],[12.,-21.],[12.,9.],[-12.,9.]]
    assert rows[0]['label_position'] == [0.,-6.]
    assert reader.snapshot() is rows and reader.loads == 1


def test_rotated_world_boundary_uses_land_pose_not_arrival_point():
    points,label=plot_geometry([100,20,200],[0,math.sqrt(.5),0,math.sqrt(.5)],(12,12,9,21))
    for p,expected in zip(points,[[79,212],[79,188],[109,188],[109,212]]):assert p==pytest.approx(expected)
    assert label==pytest.approx([94,200])


@pytest.mark.parametrize('fault',['missing','duplicate','field_id','nan','rotation','world','unregistered'])
def test_invalid_data_drops_previous_cache(fault):
    r,reader,rows=plot_runtime();reader.snapshot()
    if fault=='missing':r.write(r.parts.points+24,struct.pack('<i',11))
    if fault=='duplicate':r.write(rows[-1]+20,struct.pack('<i',1))
    if fault=='field_id':r.write(rows[-1]+16,struct.pack('<i',12))
    if fault=='nan':r.write(rows[0]+24,struct.pack('<f',math.nan))
    if fault=='rotation':r.write(rows[0]+36,b'\0'*16)
    if fault=='world':r.p64(r.parts.node+16,0)
    if fault=='unregistered':
        r.write(r.parts.entries+36,struct.pack('<i',-2));r.write(r.parts.registry+68,struct.pack('<i',2))
    with pytest.raises(ValueError):reader.snapshot()
    assert reader.cache is None


def test_configuration_dimensions_invalidate_cached_geometry():
    _,reader,_=plot_runtime();old=reader.snapshot()
    reader.limits.return_value=((1,1,2),3,(13.,12.,9.,21.))
    new=reader.snapshot()
    assert new is not old and new[0]['polygon'][0][0]==-13.


def test_pose_change_without_list_version_never_reuses_drawing():
    r,reader,rows=plot_runtime();old=reader.snapshot()
    r.write(rows[0]+24,struct.pack('<f',100.))
    new=reader.snapshot()
    assert new is not old and new[0]['label_position'][0]==100.


def test_mid_read_context_change_rejects_all_plots():
    _,reader,_=plot_runtime()
    reader.entrances.current_context=Mock(side_effect=[True,False])
    # The final context guard rejects a read even when earlier inputs were valid.
    with pytest.raises(ValueError):reader.snapshot()


@pytest.mark.parametrize('limits',[(0,12,9,21),(12,12,math.inf,21),(-1,12,9,21)])
def test_no_guessed_geometry_on_invalid_limits(limits):
    with pytest.raises(ValueError):plot_geometry([0,0,0],[0,0,0,1],limits)


def test_build_source_fingerprint_and_runtime_storage_are_verified(monkeypatch):
    import hashlib
    from types import SimpleNamespace
    import utils.drivers.house_plots as module
    payload=b'verified-source-fixture'
    monkeypatch.setattr(module,'_SOURCE_IMAGE',('XDTDataAndProtocol',len(payload),hashlib.sha256(payload).hexdigest()))
    addresses={100+0x40:200,200+0x10:300,200+0x30:400,100+0xd8:500,500:100,516:900,500+0x50:600}
    values={600:12.,604:12.,608:9.,612:21.}
    meta=Mock(domain=900)
    meta.pointer.side_effect=addresses.__getitem__
    meta.text.return_value='XDTDataAndProtocol'
    meta.integer.return_value=0
    meta.field.side_effect=lambda k,n:dict(code=12,flags=16,offset=('_fieldLeft','_fieldRight','_fieldFront','_fieldBack').index(n)*4)
    meta.data.side_effect=lambda a,n:payload if a==300 else struct.pack('<f',values[a])
    reader=HousePlotReader(SimpleNamespace(meta=meta,find_class=Mock(return_value=100)))
    assert reader.limits()[2]==(12.,12.,9.,21.)
    values[600]=13.
    assert reader.limits()[2][0]==13.
    addresses[516]=901
    with pytest.raises(ValueError,match='运行时'):reader.limits()
    reader.verified_image=None;addresses[516]=900
    meta.data.side_effect=lambda a,n:b'bad-source' if a==300 else struct.pack('<f',values[a])
    with pytest.raises(ValueError,match='来源版本'):reader.limits()


def test_navigation_reader_never_constructs_house_display_reader(monkeypatch):
    from tests.unit.test_guidance_planar_scene import plane_reader
    import utils.drivers.mono_scene as module
    reader=plane_reader();reader.navigation_only=True
    reader.house_entrances=Mock()
    reader.house_entrances.snapshot.return_value=[]
    reader.house_entrances.cache={'context':('fixture',)}
    constructor=Mock(side_effect=AssertionError('display reader entered navigation path'))
    monkeypatch.setattr(module,'HousePlotReader',constructor)
    reader.snapshot()
    constructor.assert_not_called()
