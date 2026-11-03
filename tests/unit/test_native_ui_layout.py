"""Version-pinned native UI layout reads, entirely synthetic memory."""
import struct
from unittest.mock import Mock

import pytest

from utils.drivers.native_ui_layout import NativeUiLayout


def layout():
    bridge=NativeUiLayout.__new__(NativeUiLayout)
    bridge.meta=Mock();bridge.native=Mock();m,n=bridge.meta,bridge.native
    m.obj.return_value=dict(address=101,name='RectTransform',namespace='UnityEngine',size=24)
    m.field.return_value=dict(offset=16,code=11,flags=4)
    handle=(405<<48)|2;m.data.return_value=struct.pack('<Q',handle)
    info=dict(address=201,name='RectTransform',namespace='UnityEngine',size=32)
    element=dict(name='ObjectItem',namespace='',size=32,value_type=True)
    n.class_at.return_value=dict(address=1000)
    n.static_ref.return_value=2000
    n.ref.return_value=3000
    n.obj.side_effect=lambda at:dict(address=2001,name='HostObjectMapUnitySide',namespace='MonoGame.Core') if at==2000 else dict(element=4000) if at==3000 else info
    n.cls.return_value=element
    n.field.return_value=dict(offset=16)
    n.pointer.side_effect=lambda at:6000
    raw=bytearray(64)
    struct.pack_into('<4f',raw,0,-960.,-540.,1920.,1080.)
    struct.pack_into('<2f',raw,32,100.,200.)
    cells={3024:struct.pack('<Q',8),3064:struct.pack('<QiHH',5000,0,405,1),6184:bytes(raw)}
    n.data.side_effect=lambda at,size:cells[at]
    return bridge,m,n,cells


def test_handle_slot_and_native_rect_are_rechecked():
    b,m,n,c=layout();d=b.rect(9000)
    assert d['rect']==(-960.,-540.,1920.,1080.)
    assert d['anchored_position']==(100.,200.)


@pytest.mark.parametrize('fault',['wrong_mono','wrong_native','wrong_type_id','wrong_kind',
    'slot_changed','zero_handle','out_of_bounds','bad_field','dirty_rect','nan_rect','negative_size'])
def test_stale_or_invalid_layout_never_becomes_click_coordinates(fault):
    b,m,n,c=layout()
    if fault=='wrong_mono':m.obj.return_value=dict(address=101,name='Transform',namespace='UnityEngine',size=24)
    elif fault=='wrong_native':
        original=n.obj.side_effect
        n.obj.side_effect=lambda at:dict(address=201,name='Transform',namespace='UnityEngine',size=32) if at==5000 else original(at)
    elif fault in ('wrong_type_id','wrong_kind'):c[3064]=struct.pack('<QiHH',5000,0,404 if fault=='wrong_type_id' else 405,2 if fault=='wrong_kind' else 1)
    elif fault=='slot_changed':
        calls=[]
        def read(at,size):
            if at==3064:
                calls.append(1)
                return struct.pack('<QiHH',5000 if len(calls)==1 else 5001,0,405,1)
            return c[at]
        n.data.side_effect=read
    elif fault=='zero_handle':m.data.return_value=b'\0'*8
    elif fault=='out_of_bounds':c[3024]=struct.pack('<Q',2)
    elif fault=='bad_field':m.field.return_value=dict(offset=16,code=8,flags=4)
    else:
        raw=bytearray(c[6184])
        if fault=='dirty_rect':raw[0x39]=1
        else:struct.pack_into('<f',raw,8,float('nan') if fault=='nan_rect' else -10.)
        c[6184]=bytes(raw)
    with pytest.raises(ValueError):b.rect(9000)


@pytest.mark.parametrize('fault',[None,'cycle','index_range','changed_table','bad_rotation'])
def test_native_transform_chain_is_bounded_and_revalidated(fault):
    b,m,n,c=layout()
    c[6056]=struct.pack('<Qi',7000,2)
    c[7024]=struct.pack('<QQ',8000,9000)
    c[8096]=struct.pack('<12f',10.,20.,0.,0.,0.,0.,0.,1.,2.,3.,1.,0.)
    c[9008]=struct.pack('<i',-1)
    if fault=='cycle':c[9008]=struct.pack('<i',2)
    elif fault=='index_range':c[9008]=struct.pack('<i',65536)
    elif fault=='bad_rotation':c[8096]=struct.pack('<12f',10.,20.,0.,0.,0.,0.,0.,0.,2.,3.,1.,0.)
    elif fault=='changed_table':
        calls=[]
        def read(at,size):
            if at==7024:
                calls.append(1)
                return struct.pack('<QQ',8000 if len(calls)==1 else 8001,9000)
            return c[at]
        n.data.side_effect=read
    if fault:
        with pytest.raises(ValueError):b.transforms(9000)
    else:assert b.transforms(9000)==(((10.,20.,0.),(0.,0.,0.,1.),(2.,3.,1.)),)


@pytest.mark.parametrize('value,expected',[(b'\x01\x01',True),(b'\0\0',False),(b'\x01\0',False),(b'\x01\xff',None)])
def test_visibility_is_not_assumed_from_a_widget_reference(value,expected):
    b,m,n,c=layout();m.obj.return_value.update(name='GameObject');old=n.obj.side_effect
    n.obj.side_effect=lambda at:dict(address=201,name='GameObject',namespace='UnityEngine',size=32) if at==5000 else old(at)
    c[6086]=value
    if expected is None:
        with pytest.raises(ValueError):b.active(9000)
    else:assert b.active(9000) is expected
