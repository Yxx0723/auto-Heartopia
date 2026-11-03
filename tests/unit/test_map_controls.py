"""Owned mini-map/open and map-title/back controls, no game input."""
from unittest.mock import Mock

import pytest

from utils.drivers.map_panel import MapPanelReader


def reader(monkeypatch,action):
    r=MapPanelReader.__new__(MapPanelReader);m=r.meta=Mock();r.memory=Mock();r.clock=lambda:20.
    scope=(1,2,3,(300,));r.ui_views=Mock(return_value=scope)
    r.scene_context=Mock(return_value='world');r.expected=Mock()
    r.owner=Mock(return_value=None if action=='open' else (1,2,3,300,301))
    m.obj.return_value={'name':'MapHudPanel','namespace':'XDTGame.UI.Panel'}
    refs={(300,'ui'):301,(301,'map_root_go'):302,(301,'map_bar_go'):303,
        (301,'map_bar_widget'):400,(301,'close_widget'):400,(400,'ui'):401,
        (400,'_thisView'):300,(400,'_backHandler'):0,(401,'map_btn'):500,(401,'back_btn'):500}
    m.ref.side_effect=lambda obj,name:refs[obj,name]
    r._button_rect=Mock(return_value=(10.,20.,80.,90.))
    layout=Mock();layout.active.return_value=True
    monkeypatch.setattr('utils.drivers.native_ui_layout.NativeUiLayout',Mock(return_value=layout))
    return r,m,refs,layout


@pytest.mark.parametrize('action',['open','close'])
def test_map_control_reads_owned_button_and_keeps_a_stable_identity(monkeypatch,action):
    r,m,refs,layout=reader(monkeypatch,action)
    a=r.map_control((1366,768),action);b=r.map_control((1366,768),action)
    assert a==b and a['buttons'][action]==(10.,20.,80.,90.)
    assert a['kind']=='map_'+action and a['scene_context']=='world'
    r._button_rect.assert_called_with(layout,(1,2,3,300,301),500,(1366,768))


@pytest.mark.parametrize('fault',['hidden','duplicate','scene_changed','component_changed'])
def test_open_button_cannot_be_taken_from_hidden_or_changed_hud(monkeypatch,fault):
    r,m,refs,layout=reader(monkeypatch,'open')
    if fault=='hidden':layout.active.return_value=False
    elif fault=='duplicate':r.ui_views.return_value=(1,2,3,(300,300))
    elif fault=='scene_changed':r.scene_context.side_effect=['world','other']
    else:
        r._button_rect.side_effect=lambda *a:refs.update({(401,'map_btn'):501}) or (1.,2.,3.,4.)
    with pytest.raises(ValueError):r.map_control((1366,768),'open')


@pytest.mark.parametrize('fault',['wrong_view','override','changed_button'])
def test_close_requires_default_back_handler_owned_by_this_map(monkeypatch,fault):
    r,m,refs,layout=reader(monkeypatch,'close')
    if fault=='wrong_view':refs[400,'_thisView']=999
    elif fault=='override':refs[400,'_backHandler']=999
    else:r._button_rect.side_effect=lambda *a:refs.update({(401,'back_btn'):501}) or (1.,2.,3.,4.)
    with pytest.raises(ValueError):r.map_control((1366,768),'close')


def test_map_is_already_open_or_closed_without_using_a_pooled_button(monkeypatch):
    r,m,refs,layout=reader(monkeypatch,'open');r.owner.return_value=(1,2,3,300,301)
    assert r.map_control((1366,768),'open')['state']=='closed'
    r.owner.return_value=None
    assert r.map_control((1366,768),'close')['state']=='closed'
    r._button_rect.assert_not_called()
