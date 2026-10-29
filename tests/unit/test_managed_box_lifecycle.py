"""Detached boxes need a pinned lifecycle and fresh absence of native backing."""
import struct
from unittest.mock import Mock

import pytest

from tests.unit.test_guidance_planar_scene import plane_reader
from utils.core.scene_read import navigation_quality_valid, SceneReadIssue


def detached_reader(monkeypatch, *, confirmed=True, final=True):
    reader=plane_reader();m=reader.meta
    original_obj=m.obj.side_effect;original_ref=m.ref.side_effect
    def obj(at):
        if at==0:raise ValueError('场景数据范围无效')
        info=original_obj(at)
        if at==3000:info={**info,'namespace':'XDTGame.Core.Physics'}
        return info
    def ref(at,name):
        if at==3000 and name in ('_group','_hierarchy'):return 0
        return original_ref(at,name)
    m.obj.side_effect=obj;m.ref.side_effect=ref
    try:
        from utils.drivers.managed_box_lifecycle import DetachedBoxGuard
    except ImportError:
        pass  # Run the same actual snapshot regression before implementation.
    else:
        monkeypatch.setattr(DetachedBoxGuard,'image_verified',lambda *args:confirmed)
        checks=iter([True,final])
        monkeypatch.setattr(DetachedBoxGuard,'detached',lambda *args:next(checks))
    return reader


def test_enabled_but_confirmed_detached_box_does_not_poison_whole_snapshot(monkeypatch):
    import time
    scene=detached_reader(monkeypatch).snapshot()
    assert scene['quality']['managed_unknown']==0
    assert navigation_quality_valid(scene,time.monotonic())
    assert scene['colliders']==[]
    assert scene['statistics']['detached_boxes_filtered']==1


def test_unrecognized_image_does_not_authorize_dropping_a_rootless_box(monkeypatch):
    scene=detached_reader(monkeypatch,confirmed=False).snapshot()
    assert not scene['quality']['navigation_eligible']
    assert scene['quality']['managed_unknown']==1


def test_reattachment_before_publication_revokes_provisional_detached_proof(monkeypatch):
    scene=detached_reader(monkeypatch,final=False).snapshot()
    assert not scene['quality']['navigation_eligible']
    assert scene['quality']['managed_unknown']==1


def test_ordinary_wall_geometry_is_unchanged():
    scene=plane_reader((1.,2.,8.)).snapshot()
    assert scene['quality']['navigation_eligible'] and len(scene['colliders'])==1
    assert scene['colliders'][0]['height']==[-1.,1.]


def guard_fixture():
    from utils.drivers.managed_box_lifecycle import DetachedBoxGuard
    meta=Mock(domain=9)
    info={'address':100,'name':'DynamicBoxCollider','namespace':'XDTGame.Core.Physics','size':80}
    meta.obj.return_value=info
    offsets={'_stream':24,'_group':32,'_hierarchy':40,'_collider':64}
    meta.field.side_effect=lambda cls,name:{'offset':offsets[name],'code':18,'flags':0}
    body=bytearray(48);struct.pack_into('<Q',body,0,2000)
    meta.data.return_value=bytes(body)
    guard=DetachedBoxGuard(meta,lambda:None,{})
    return guard,meta,info,body


@pytest.mark.parametrize('offset', [8,16,40])
def test_any_group_hierarchy_or_native_backing_keeps_box_unconfirmed(offset):
    guard,meta,info,body=guard_fixture()
    struct.pack_into('<Q',body,offset,70000);meta.data.return_value=bytes(body)
    assert not guard.detached(3000,info,2000)


def test_matching_detached_state_requires_correct_stream_and_class():
    guard,meta,info,body=guard_fixture()
    assert guard.detached(3000,info,2000)
    assert not guard.detached(3000,info,2001)
    meta.obj.return_value={**info,'address':101}
    assert not guard.detached(3000,info,2000)


def test_torn_state_or_second_read_reattachment_is_not_accepted():
    guard,meta,info,body=guard_fixture()
    changed=bytearray(body);struct.pack_into('<Q',changed,8,70000)
    meta.data.side_effect=[bytes(body),bytes(changed)]
    assert not guard.detached(3000,info,2000)


@pytest.mark.parametrize('field', [dict(offset=1,code=18,flags=0),dict(offset=79,code=18,flags=0),
    dict(offset=24,code=8,flags=0),dict(offset=24,code=18,flags=16)])
def test_invalid_field_layout_does_not_read_or_confirm_lifecycle(field):
    guard,meta,info,body=guard_fixture();meta.field.side_effect=None;meta.field.return_value=field
    assert not guard.detached(3000,info,2000)
    meta.data.assert_not_called()


def test_native_read_failure_is_unknown_not_detached():
    guard,meta,info,body=guard_fixture();meta.data.side_effect=ValueError('short read')
    assert not guard.detached(3000,info,2000)


def test_cancellation_is_not_converted_into_inactive_object():
    guard,meta,info,body=guard_fixture()
    guard.check=Mock(side_effect=SceneReadIssue('stop',kind='cancelled'))
    with pytest.raises(SceneReadIssue):guard.detached(3000,info,2000)


def test_wrong_type_and_unknown_image_never_create_provisional_proof(monkeypatch):
    guard,meta,info,body=guard_fixture()
    monkeypatch.setattr(guard,'image_verified',lambda *args:False)
    assert not guard.consider(3000,info,2000)
    monkeypatch.setattr(guard,'image_verified',lambda *args:True)
    assert not guard.consider(3000,{**info,'namespace':'Other'},2000)
    assert guard.revalidate()==0 and not guard.proofs


def test_pinned_digest_is_valid_sha256():
    from utils.drivers.managed_box_lifecycle import PHYSICS_IMAGE_SHA256
    assert len(PHYSICS_IMAGE_SHA256)==64
    assert len(bytes.fromhex(PHYSICS_IMAGE_SHA256))==32


def image_fixture(monkeypatch, *, recognized=True):
    import hashlib
    from utils.drivers import managed_box_lifecycle as module
    payload=b'synthetic immutable PE image'
    monkeypatch.setattr(module,'PHYSICS_IMAGE_SHA256',hashlib.sha256(payload).hexdigest()
                        if recognized else '0'*64)
    guard,meta,info,body=guard_fixture()
    meta.pointer.side_effect=lambda at:{164:1000,1048:2000}[at]
    meta.text.return_value='XDTBaseService'
    meta.data.side_effect=lambda at,n:struct.pack('<QI',3000,len(payload)) if at==1016 else payload
    return guard,meta,info


def test_only_a_verified_immutable_image_is_cached(monkeypatch):
    guard,meta,info=image_fixture(monkeypatch)
    assert guard.image_verified(info) and guard.image_verified(info)
    assert sum(call.args[0]==3000 for call in meta.data.call_args_list)==1
    assert len(guard.images)==1


def test_unknown_image_stays_unconfirmed_without_rehashing_every_box(monkeypatch):
    guard,meta,info=image_fixture(monkeypatch,recognized=False)
    assert not guard.image_verified(info) and not guard.image_verified(info)
    assert sum(call.args[0]==3000 for call in meta.data.call_args_list)==1
    assert not guard.images


def test_new_image_pointer_does_not_reuse_previous_digest(monkeypatch):
    guard,meta,info=image_fixture(monkeypatch)
    assert guard.image_verified(info)
    meta.data.side_effect=lambda at,n:struct.pack('<QI',4000,4) if at==1016 else b'new!'
    assert not guard.image_verified(info)
