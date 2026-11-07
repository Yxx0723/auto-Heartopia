"""P0 acceptance with fake inputs; never imports or creates a real game reader."""
import json
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_player import Game
from tests.unit.test_default_cooking import inventory, view
from utils.core.input_keys import KEY_VK
from utils.drivers.input_task_gate import InputTaskGate
from utils.flows.cooking_entry import run_stove_entry


def kitchen(tmp_path,opened=False):
    game=Game();menu={'state':'ready','open':opened,'scope':'cooker'}
    original=game.execute
    def execute(event):
        if event['type']=='key_down':
            assert event=={'type':'key_down','key':'1','vk':0x31,'extended':False}
            persisted=json.loads((tmp_path/'entry.json').read_text(encoding='utf-8'))
            assert persisted['entry_dispatched'] is True
            menu['open']=True
        original(event)
    game.execute=execute
    args=dict(window=game,controls=game,device=game,read_menu=lambda:dict(menu),
              read_recipe=view,inventory=inventory,clock=game,report=Mock(),
              input_gate=InputTaskGate(),journal=tmp_path/'entry.json',
              binding='synthetic-game-session',stove_ready=True,timeout=.6)
    return game,menu,args


def run(args,**changes):
    return run_stove_entry(**{**args,**changes})


def test_top_row_one_opens_and_validates_default_slots_without_starting_a_pot(tmp_path):
    game,menu,args=kitchen(tmp_path)
    record=run(args)
    assert record['state']=='RECIPE_VALIDATED' and record['started_pots']==0
    assert record['recipe_id']==55 and record['ingredients']=={10:2}
    assert record['material_slots']==view()['selected']['slots']
    downs=[e for _,e in game.events if e['type']=='key_down']
    assert len(downs)==1 and KEY_VK[downs[0]['key']]==0x31!=KEY_VK['numpad_1']
    assert not game.keys and args['input_gate'].owner is None


def test_already_open_panel_needs_no_interaction_input(tmp_path):
    game,menu,args=kitchen(tmp_path,opened=True)
    assert run(args,stove_ready=False)['state']=='RECIPE_VALIDATED'
    assert not game.events


def test_cached_recipe_without_verified_panel_state_never_authorizes_input(tmp_path):
    game,menu,args=kitchen(tmp_path)
    assert run(args,read_menu=lambda:{'state':'unknown'})['state']=='UNCONFIRMED'
    assert not game.events
    with pytest.raises(ValueError,match='禁止发送'):
        run(args,read_menu=None)
    assert not game.events


def test_manual_stove_readiness_is_required_before_entry(tmp_path):
    game,menu,args=kitchen(tmp_path)
    assert run(args,stove_ready=False)['state']=='STOVE_NOT_READY'
    assert not game.events


def test_open_failure_sends_once_and_restart_does_not_toggle_again(tmp_path):
    game,menu,args=kitchen(tmp_path)
    game.execute=Game.execute.__get__(game,Game)  # Input has no simulated effect.
    assert run(args)['state']=='UNCONFIRMED'
    assert run(args)['state']=='UNCONFIRMED'
    assert sum(e['type']=='key_down' for _,e in game.events)==1
    menu['open']=True
    assert run(args)['state']=='RECIPE_VALIDATED'
    assert sum(e['type']=='key_down' for _,e in game.events)==1


@pytest.mark.parametrize('fault',['empty','foreign','locked','insufficient','star'])
def test_material_failures_never_start_or_replace_recipe(tmp_path,fault):
    game,menu,args=kitchen(tmp_path,opened=True)
    def bag():
        data=inventory()
        if fault=='locked':data['items'][0]['locked']=True
        elif fault=='insufficient':data['items'][0]['count']=1
        elif fault=='star':data['items'][0]['star']=5
        return data
    def recipe():
        data=view()
        if fault=='empty':data['selected']['slots'][0]={'filled':False}
        elif fault=='foreign':data['selected']['slots'][0]={'filled':True,'net_id':999,'item_id':10}
        return data
    assert run(args,inventory=bag,read_recipe=recipe,max_star=1)['state']=='UNCONFIRMED'
    assert not game.events


@pytest.mark.parametrize('interrupt',['stop','pause','focus','size'])
def test_interruption_releases_and_retains_pending_entry(tmp_path,interrupt):
    game,menu,args=kitchen(tmp_path)
    if interrupt=='stop':game.stop_at=.02
    elif interrupt=='pause':game.pause_at=.02
    elif interrupt=='focus':game.foreground=lambda:game.time<.02
    else:game.size=lambda:[1280,720] if game.time<.02 else [640,480]
    result=run(args)
    assert result['state']==('STOPPED' if interrupt=='stop' else 'PAUSED')
    assert result['entry_dispatched'] and not game.keys and game.time<=.021


def test_wrong_scope_rejects_cached_detail_before_any_input(tmp_path):
    game,menu,args=kitchen(tmp_path);menu['scope']='another-cooker'
    assert run(args)['state']=='INVALID' and not game.events


def test_recipe_or_inventory_read_failure_only_retries_reads(tmp_path):
    game,menu,args=kitchen(tmp_path,opened=True)
    read=Mock(side_effect=[OSError('temporary'),view(),view()])
    assert run(args,read_recipe=read)['state']=='RECIPE_VALIDATED'
    assert read.call_count==3 and not game.events


def test_input_failure_is_persisted_and_never_retried_on_restart(tmp_path):
    game,menu,args=kitchen(tmp_path)
    game.execute=Mock(side_effect=OSError('input rejected'))
    result=run(args)
    assert result['state']=='ERROR' and result['last_action_error']['message']=='input rejected'
    assert run(args)['state']=='UNCONFIRMED'
    assert game.execute.call_count==1


def test_foreign_or_damaged_journal_cannot_issue_a_new_entry(tmp_path):
    game,menu,args=kitchen(tmp_path)
    (tmp_path/'entry.json').write_text('{bad',encoding='utf-8')
    with pytest.raises(ValueError):run(args)
    assert not game.events


def test_existing_task_owns_input_no_release_or_read_occurs(tmp_path):
    game,menu,args=kitchen(tmp_path)
    game.release_all=Mock();read=Mock()
    with args['input_gate'].claim('采集回放'):
        with pytest.raises(ValueError,match='占用'):run(args,read_recipe=read)
    read.assert_not_called();game.release_all.assert_not_called()


def test_reading_twice_uses_detached_evidence_not_mutated_callback_objects(tmp_path):
    game,menu,args=kitchen(tmp_path,opened=True);bag=inventory();calls=[]
    def stock():
        calls.append(1);bag['counts'][10]+=1
        return bag
    result=run(args,inventory=stock)
    assert result['state']=='UNCONFIRMED' and len(calls)>2 and not game.events
