import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.unit.test_cooking_entry import kitchen
from utils.drivers.input_task_gate import InputTaskGate
from utils.flows.cooking_entry import run_stove_entry
from utils.ui.cooking_entry_control import CookingEntryControl


def control(tmp_path):
    game,menu,args=kitchen(tmp_path);game.hwnd=1
    def execute(event):
        if event['type']=='key_down':
            assert event['key']=='1' and event['vk']==0x31
            menu['open']=True
        type(game).execute(game,event)
    game.execute=execute
    reader=SimpleNamespace(cooking_inventory_snapshot=lambda _:args['inventory'](),close=Mock())
    source=SimpleNamespace(snapshot=args['read_menu'],recipe=SimpleNamespace(snapshot=args['read_recipe']))
    controls=SimpleNamespace(start=Mock(),snapshot=game.snapshot,pause=game.pause,close=Mock())
    gate=InputTaskGate()
    def runner(**kw):
        assert gate.owner=='自动烹饪'  # Shared gate remains held through entry verification.
        assert kw['input_gate'] is not gate
        return run_stove_entry(**kw)
    c=CookingEntryControl(tmp_path/'profile.json',tmp_path,clock=game,runner=runner,input_gate=gate,
        window_factory=lambda _:game,reader_factory=lambda *a,**k:reader,
        controls_factory=lambda *a:controls,device_factory=lambda _:game,
        menu_factory=lambda _:source)
    return c,game,controls,reader,gate


def test_visible_task_opens_once_logs_result_and_releases_every_resource(tmp_path):
    c,g,controls,reader,gate=control(tmp_path)
    c.start(123);c.thread.join(2)
    assert not c.active and not g.keys and gate.owner is None
    summary=json.loads((c.path/'summary.json').read_text(encoding='utf-8'))
    assert summary['entry']['state']=='RECIPE_VALIDATED' and summary['started_pots']==0
    assert c.take_latest()['state']=='RECIPE_VALIDATED'
    controls.close.assert_called_once();reader.close.assert_called_once()
    assert sum(e['type']=='key_down' for _,e in g.events)==1


def test_stop_while_waiting_f6_never_sends_entry_input(tmp_path):
    c,g,controls,reader,gate=control(tmp_path)
    controls.snapshot=Mock(side_effect=[(False,False),(False,True)])
    c.start(123);c.thread.join(2)
    assert not c.active and not g.events and gate.owner is None
    assert c.take_latest()['state']=='stopped'


def test_busy_task_cannot_launch_another_controller(tmp_path):
    c,*_=control(tmp_path);c.busy=lambda:True;c.window_factory=Mock()
    with pytest.raises(ValueError,match='已有'):c.start(123)
    c.window_factory.assert_not_called()


def test_unconfirmed_previous_entry_is_reused_after_restart(tmp_path):
    c,g,*_=control(tmp_path)
    old=tmp_path/'入口验证-old';old.mkdir()
    journal=old/'entry.json'
    journal.write_text(json.dumps({'schema':'cooking-entry-p0-v1','binding':'p0-game-123',
        'entry_dispatched':True,'state':'UNCONFIRMED','started_pots':0}),encoding='utf-8')
    c.start(123);c.thread.join(2)
    assert not c.active and not g.events
    assert c.entry_journal('p0-game-123')==journal
    assert json.loads((c.path/'summary.json').read_text(encoding='utf-8'))['entry']['state']=='UNCONFIRMED'
