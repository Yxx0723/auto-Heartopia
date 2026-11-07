"""Bounded tool confirmation; simulated input only, no live game."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.unit.test_coordinate_auto_equip import Selector, run
from tests.unit.test_coordinate_auto_repair import RepairGame
from utils.flows.coordinate_player import _Follower, _Paused, _Stopped


def setup_follower():
    game=RepairGame(current=890)
    report=Mock()
    f=_Follower({'duration':10.,'client_size':game.size()},game,game,game,game,report,game,
                waypoints=[{'position':[0.,0.,0.]}],tool_selector=Selector(game))
    f.collection={'sent':1,'remaining':2.4,'ready':False}
    return game,f,report


@pytest.mark.parametrize('failures',[[ValueError('temporary tool read')],
                                    [OSError('read denied'),OSError('read changed')]])
def test_transient_errors_recover_without_moving_or_changing_collection(failures):
    game,f,report=setup_follower();before=deepcopy(f.collection);identity=f.collection
    good=game.tools_snapshot();times=[]
    values=iter([*failures,good])
    def read():
        times.append(game.time)
        value=next(values)
        if isinstance(value,Exception):raise value
        return value
    game.tools_snapshot=Mock(side_effect=read)
    f.set_keys({'w'})
    assert f.maintain_tool(0,1) is False
    assert game.tools_snapshot.call_count==len(failures)+1
    assert times==pytest.approx([i*.2 for i in range(len(times))])
    assert f.collection is identity and f.collection==before
    assert game.position==[0.,0.,0.] and not game.keys
    assert f.tool_read_failures==0 and f.tool_read_kind=='equipped'
    retry=next(c for c in report.call_args_list if c.args[0]=='tool_retry')
    assert retry.kwargs['tool_read_error']['type']==type(failures[0]).__name__
    assert retry.kwargs['tool_read_error']['message']==str(failures[0])


def test_five_failed_reads_pause_with_last_real_error_and_keep_progress():
    game,f,report=setup_follower();before=deepcopy(f.collection)
    game.tools_snapshot=Mock(side_effect=[OSError('failure '+str(i)) for i in range(1,6)])
    with pytest.raises(_Paused):f.maintain_tool(0,1)
    assert game.tools_snapshot.call_count==5 and game.time==pytest.approx(.8)
    assert not game.keys and f.collection==before and f.tool_read_failures==5
    assert report.call_args.args[0]=='equip_paused'
    assert 'OSError: failure 5' in report.call_args.args[1]
    assert report.call_args.kwargs['tool_last_exception']=={'type':'OSError','message':'failure 5'}


def test_failures_read_before_maintenance_count_towards_the_same_five_read_limit():
    game,f,report=setup_follower()
    game.tools_snapshot=Mock(side_effect=ValueError('still unavailable'))
    f.read_tool();f.read_tool()
    with pytest.raises(_Paused):f.maintain_tool(0,1)
    assert game.tools_snapshot.call_count==5 and f.tool_read_failures==5


@pytest.mark.parametrize('value',[None,{'state':'ready','equipped':True},
                                  {'state':'ready','equipped':'yes','current':890,'maximum':1100}])
def test_incomplete_shape_is_not_confirmed_as_held(value):
    game,f,report=setup_follower();game.tools_snapshot=Mock(return_value=value)
    with pytest.raises(_Paused):f.maintain_tool(0,1)
    assert game.tools_snapshot.call_count==5 and f.tool_read_kind=='incomplete'
    assert f.tool_read_error['type']=='ToolStateIncomplete'
    assert not game.events


def test_confirmed_unheld_state_uses_existing_equip_flow():
    game,f,report=setup_follower();game.held=False
    f.equip_axe_pick=Mock(return_value=True)
    assert f.maintain_tool(0,1) is True
    f.equip_axe_pick.assert_called_once_with(0,1)
    assert f.tool_read_kind=='not_equipped' and f.tool_read_failures==0


def test_confirmed_locked_tool_pauses_immediately():
    game,f,report=setup_follower();game.tools_snapshot=Mock(return_value={'state':'locked'})
    with pytest.raises(_Paused):f.maintain_tool(0,1)
    assert game.tools_snapshot.call_count==1 and game.time==0.
    assert f.tool_read_kind=='locked' and f.tool_read_failures==0
    assert not game.events


@pytest.mark.parametrize('interrupt',['stop','pause','focus'])
def test_retry_wait_still_obeys_stop_pause_and_focus(interrupt):
    game,f,report=setup_follower();game.tools_snapshot=Mock(side_effect=ValueError('temporary'))
    if interrupt=='stop':game.stop_at=.05
    elif interrupt=='pause':game.pause_at=.05
    else:game.foreground=lambda:game.time<.05
    before=deepcopy(f.collection)
    with pytest.raises(_Stopped if interrupt=='stop' else _Paused):f.maintain_tool(0,1)
    assert game.time<=.061 and not game.keys and f.collection==before
    assert game.tools_snapshot.call_count==1


def test_recovery_during_collection_does_not_send_f_again():
    game=RepairGame(current=890);original_read=game.tools_snapshot;original_input=game.execute
    failures=[False,False]
    def execute(event):
        original_input(event)
        if event=={'type':'key_down','key':'f'}:failures[0]=True
    def read():
        if failures[0] and not failures[1]:
            failures[1]=True
            raise ValueError('tool state updating after collection')
        return original_read()
    game.execute=execute;game.tools_snapshot=read
    result,selector,statuses=run(game,collect_wait=2.)
    assert result and failures==[True,True]
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==1
    assert any(state=='tool_retry' for _,state,_,_ in statuses)
    assert any(state=='reached' for _,state,_,_ in statuses)
    assert not any(state=='equip_paused' for _,state,_,_ in statuses)


def test_read_timestamp_and_last_real_exception_survive_incomplete_retry():
    game,f,report=setup_follower()
    game.tools_snapshot=Mock(side_effect=[OSError('original error'),{'state':'ready'}])
    f.read_tool();game.time=.2;status=f.read_tool()
    assert status['read_at']==status['read_finished_at']==pytest.approx(.2)
    assert status['consecutive_failures']==2 and status['read_kind']=='incomplete'
    assert f.tool_last_exception=={'type':'OSError','message':'original error'}


def test_partial_multi_f_collection_keeps_sent_and_remaining_until_it_resumes():
    game,f,report=setup_follower();f.collection['remaining']=.25
    f.set_keys({'f'});f.release()  # The first F was already sent before failure.
    good=game.tools_snapshot()
    game.tools_snapshot=Mock(side_effect=[ValueError('temporary'),good])
    f.maintain_tool(0,1)
    assert f.collection=={'sent':1,'remaining':.25,'ready':False}
    game.tools_snapshot=Mock(return_value=good)
    point={'position':[0.,0.,0.],'collect_count':3,'collect_wait':.25}
    while not f.collect_point(point,0,1):pass
    assert f.collection['sent']==3
    assert sum(e=={'type':'key_down','key':'f'} for _,e in game.events)==3
