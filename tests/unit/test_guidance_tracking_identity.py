"""Tracking identity is distinct from mutable target coordinates and path tokens."""
import struct

import pytest

from tests.unit.test_guidance_route_cache import cached_game
from tests.unit.test_native_guidance_follow import run,snapshot
from tests.unit.test_native_guidance_reader import reader
from utils.core.guidance_path import GuidanceRouteCache,validate_guidance


def identified_game(*, changed_id=False, changed_scene=False, drift=True):
    game=cached_game()
    def raw():
        changed=game.time>=.6
        return dict(snapshot(now=game.time,target=(8,1,5) if changed and drift else (8,0,4)),
                    tracking_id='2' if changed and changed_id else '1',
                    context='other-level' if changed and changed_scene else 'game-level-a',path_token='one')
    game.guidance.side_effect=raw
    game.guidance_status.side_effect=lambda:{k:v for k,v in raw().items() if k!='path'}
    return game


def test_same_tracking_id_coordinate_update_refreshes_route_without_false_pause():
    game=identified_game();reports=[]
    result=run(game,lambda state,message,**extra:reports.append((state,extra)))
    assert result['arrived'] and game.running
    assert not [x for x in reports if x[0]=='paused']
    assert len([x for x in reports if x[0]=='guidance_path'])==2
    assert not game.keys


def test_different_tracking_id_at_identical_coordinates_stops_before_more_input():
    game=identified_game(changed_id=True,drift=False);game.stop_at=2.;reports=[]
    result=run(game,lambda state,message,**extra:reports.append((state,extra)))
    assert not result['arrived'] and not game.running
    assert not [(t,e) for t,e in game.events if t>.75 and e['type']=='key_down']
    assert any(extra.get('guidance_pause_reason')=='tracking_id_changed' for _,extra in reports)


def test_same_tracking_id_cannot_bypass_scene_change():
    game=identified_game(changed_scene=True,drift=False);game.stop_at=2.;reports=[]
    assert not run(game,lambda s,m,**e:reports.append(e))['arrived']
    assert any(e.get('guidance_pause_reason')=='scene_context_changed' for e in reports)
    assert not game.keys


def test_same_id_status_target_update_requires_a_new_full_payload():
    cache=GuidanceRouteCache();cache.install(dict(snapshot(),tracking_id='1',path_token='same'),0.)
    value=dict(snapshot(now=.1,target=(8,1,5)),tracking_id='1',path_token='same')
    assert not cache.confirm(value,.1)
    cache.install(value,.1)
    assert cache.route['target']==(8,1,5) and cache.route['tracking_id']=='1'


def test_module_rebuild_in_same_scene_and_task_needs_full_refresh_not_false_pause():
    game=identified_game(drift=False);normal=game.guidance.side_effect
    def raw():
        return dict(normal(),scene_context='same-world',context='new-module' if game.time>=.6 else 'old-module')
    game.guidance.side_effect=raw
    game.guidance_status.side_effect=lambda:{k:v for k,v in raw().items() if k!='path'}
    reports=[]
    assert run(game,lambda s,m,**e:reports.append((s,e)))['arrived']
    assert not [s for s,e in reports if s=='paused']
    assert len([s for s,e in reports if s=='guidance_path'])==2


def test_same_module_and_task_id_cannot_override_explicit_scene_change():
    cache=GuidanceRouteCache();cache.install(dict(snapshot(),tracking_id='1',scene_context='world-a',path_token='x'),0.)
    assert not cache.confirm(dict(snapshot(now=.1),tracking_id='1',scene_context='world-b',path_token='x'),.1)


def test_missing_identity_after_acceptance_pauses_instead_of_legacy_fallback():
    game=identified_game(drift=False);game.stop_at=2.;normal=game.guidance_status.side_effect;reports=[]
    def status():
        raw=normal()
        if game.time>=.6:raw.pop('tracking_id')
        return raw
    game.guidance_status.side_effect=status
    assert not run(game,lambda s,m,**e:reports.append(e))['arrived']
    assert any(e.get('guidance_pause_reason')=='tracking_identity_unavailable' for e in reports)
    assert not game.keys


def test_identity_change_between_status_and_full_refresh_cannot_install_new_task():
    game=identified_game();game.stop_at=2.;normal=game.guidance.side_effect;reports=[]
    game.guidance.side_effect=lambda:dict(normal(),tracking_id='2' if game.time>=.6 else '1')
    assert not run(game,lambda s,m,**e:reports.append(e))['arrived']
    assert any(e.get('guidance_pause_reason')=='tracking_id_changed' for e in reports)
    assert not [(t,e) for t,e in game.events if t>.75 and e['type']=='key_down']


@pytest.mark.parametrize('scene_context',[None,1,''])
def test_invalid_explicit_scene_identity_never_uses_module_fallback(scene_context):
    with pytest.raises(ValueError):validate_guidance(dict(snapshot(),tracking_id='1',scene_context=scene_context),0.)


@pytest.mark.parametrize('tracking_id',['', '0', '-1', '01', '1.0', '18446744073709551616', True, 1, None])
def test_present_but_invalid_identity_never_falls_back_to_coordinate_matching(tracking_id):
    with pytest.raises(ValueError):validate_guidance(dict(snapshot(),tracking_id=tracking_id),0.)


def test_failed_full_route_refresh_does_not_continue_on_old_target():
    game=identified_game();game.stop_at=3.;normal=game.guidance.side_effect
    def source():
        if game.time>=.6:raise ValueError('new route not ready')
        return normal()
    game.guidance.side_effect=source
    assert not run(game)['arrived']
    assert not [(t,e) for t,e in game.events if t>.75 and e['type']=='key_down']


def token_reader(token=123):
    value=reader();value.meta.fields['module']['token']=(40,11)
    value.meta.memory[140:148]=struct.pack('<Q',token)
    return value


def test_native_reader_preserves_full_uint64_identity_as_decimal_string():
    value=token_reader((1<<64)-1).snapshot()
    assert value['tracking_id']=='18446744073709551615'


@pytest.mark.parametrize('method',['snapshot','status'])
def test_mid_read_identity_change_cannot_publish_a_mixed_route(method):
    value=token_reader()
    def hook(at,size):
        if at==432:value.meta.memory[140:148]=struct.pack('<Q',124)
    value.meta.hook=hook
    with pytest.raises(ValueError):getattr(value,method)()


def test_active_navigation_with_zero_identity_is_not_legacy_guidance():
    with pytest.raises(ValueError):token_reader(0).snapshot()


@pytest.mark.parametrize('field',[(40,8),(4,11),(60,11)])
def test_native_tracking_field_layout_must_be_exact(field):
    value=token_reader();value.meta.fields['module']['token']=field
    with pytest.raises(ValueError):value.snapshot()


def test_recalculation_with_live_identity_is_pending_not_cancelled():
    value=token_reader();value.meta.memory[116]=0
    value.meta.memory[120:132]=struct.pack('<3f',float('inf'),0,0)
    value.meta.ref=lambda *args:(_ for _ in ()).throw(AssertionError('must not read old path'))
    assert value.status()['state']=='pending'


def test_pending_update_recovers_without_f6_or_old_input():
    game=identified_game();normal=game.guidance_status.side_effect
    game.guidance_status.side_effect=lambda:({'state':'pending','captured_at':game.time,'tracking_id':'1'}
                                            if .6<=game.time<1.2 else normal())
    assert run(game)['arrived']
    assert game.running and not game.keys
