"""A revoked gesture is released and re-observed, never blindly resumed."""
from dataclasses import replace

import pytest

from tests.unit.test_map_panning import panning_map


def delayed_drag(g,t,*,every=False,reason=None):
    sleep=g.sleep;count=[]
    def delayed(seconds):
        sleep(seconds)
        if g.buttons and (every or not count):
            count.append(1)
            if reason:
                t.lease.revocation_reason=reason;t.lease.tripped=True;t.lease.release(reason)
            else:
                g.time+=.3;t.lease.poll()
    g.sleep=delayed
    return count


def test_one_drag_timeout_is_released_and_replanned_from_live_map():
    g,t=panning_map();events=[];t.report=lambda state,msg,**kw:events.append(kw)
    delayed_drag(g,t)
    try:
        plan=t.prepare((0.,2.,800.))
        assert 106<plan.pixel[1]<894
        assert not g.keys and not g.buttons and not g.markers
        assert any(e.get('map_revoke_reason')=='lease_expired' for e in events)
        assert any(e.get('map_drag_retry')==1 for e in events)
    finally:t.lease.close()


def test_repeated_timeout_is_bounded_instead_of_restarting_forever():
    g,t=panning_map();count=delayed_drag(g,t,every=True)
    try:
        with pytest.raises(ValueError):t.prepare((0.,2.,800.))
        assert len(count)==3 and g.time<10.
        assert not g.buttons and not g.markers
    finally:t.lease.close()


@pytest.mark.parametrize('reason',['focus_or_size','pause','stop','watchdog_query_error'])
def test_non_timeout_revocation_is_not_retried(reason):
    g,t=panning_map();count=delayed_drag(g,t,reason=reason)
    try:
        with pytest.raises(ValueError,match=reason):t.prepare((0.,2.,800.))
        assert len(count)==1 and not g.buttons and not g.markers
    finally:t.lease.close()


def test_expired_drag_with_changed_scene_cannot_be_rearmed():
    g,t=panning_map();original=t.panel.geometry;count=delayed_drag(g,t)
    t.panel.geometry=lambda size:replace(original(size),scene='other') if count else original(size)
    try:
        with pytest.raises(ValueError):t.prepare((0.,2.,800.))
        assert len(count)==1 and not g.buttons and not g.markers
    finally:
        if t.lease:t.lease.close()


def test_mouse_only_expiry_preserves_reason_and_release_metrics():
    g,t=panning_map();g.buttons.add('left');t.lease.arm(.1);g.time=.2;t.lease.poll()
    assert t.lease.revocation_reason=='lease_expired'
    assert t.lease.metrics()['release_counts']['lease_expired']==1
    assert not g.buttons


def test_partial_drag_in_wrong_direction_is_not_accepted_during_recovery():
    g,t=panning_map();count=delayed_drag(g,t);original=t.panel.geometry
    t.panel.geometry=lambda size:replace(original(size),pan=(0.,500.)) if count else original(size)
    try:
        with pytest.raises(ValueError,match='远离目标'):t.prepare((0.,2.,800.))
        assert not g.buttons and len(count)==1
    finally:
        if t.lease:t.lease.close()


@pytest.mark.parametrize('value',[True,-1,6])
def test_retry_count_is_bounded(value):
    from utils.core.map_target_policy import MapTargetPolicy
    with pytest.raises(ValueError):MapTargetPolicy(max_drag_retries=value)
