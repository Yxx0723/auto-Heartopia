"""The real CookPanel contract, with synthetic memory only."""
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils.drivers.cooking_menu_state import CookingMenuStateReader


def reader(opened=True):
    r=CookingMenuStateReader.__new__(CookingMenuStateReader)
    r.memory=SimpleNamespace(locator=SimpleNamespace(snapshot=Mock()))
    r.recipe=SimpleNamespace(context=Mock(return_value=(1,2,3)),
        meta=SimpleNamespace(domain=9,ref=Mock(return_value=100)))
    refs={'prefab':20,'ui':30,'_selectedRecipeDetail':100,
          'background_go':40,'list_go':41,'btnBar_go':42}
    values={'_viewActiveCache':b'\x01','_isGotFocus':b'\x01',
        '_cookerStaticId':struct.pack('<i',370033),'_cookerNetId':struct.pack('<I',7),
        '_cookerObjectId':struct.pack('<Q',8)}
    meta=SimpleNamespace(obj=lambda _:dict(name='CookPanel',namespace='XDTGame.UI.Panel'),
                         ref=lambda _,name:refs[name])
    r.ui=SimpleNamespace(meta=meta,ui_views=Mock(return_value=(1,2,3,(10,) if opened else ())),
                         expected=Mock(),payload=lambda _,name,*args:values[name])
    r.layout=SimpleNamespace(active=Mock(return_value=True))
    return r,refs,values


def test_open_panel_matches_owned_detail_active_native_root_and_stove_identity():
    r,_,_=reader()
    value=r.snapshot()
    assert value['open'] is True and value['scope'] and value['read_only']
    assert value['stove_identity']==[370033,7,8]
    assert r.memory.locator.snapshot.call_count==2


def test_no_panel_is_closed_even_with_a_cached_recipe():
    r,_,_=reader(False)
    assert r.snapshot()['open'] is False
    r.layout.active.assert_not_called()


@pytest.mark.parametrize('fault',['inactive','unfocused','native_hidden','detail_mismatch','stack','world','duplicate','stove_changed'])
def test_unknown_or_changing_panel_cannot_be_promoted_to_open(fault):
    r,refs,values=reader()
    if fault=='inactive':values['_viewActiveCache']=b'\0'
    elif fault=='unfocused':values['_isGotFocus']=b'\0'
    elif fault=='native_hidden':r.layout.active.return_value=False
    elif fault=='detail_mismatch':refs['_selectedRecipeDetail']=200
    elif fault=='stack':r.ui.ui_views.side_effect=[(1,2,3,(10,)),(1,2,3,())]
    elif fault=='world':r.recipe.context.side_effect=[(1,2,3),(1,2,4)]
    elif fault=='duplicate':r.ui.ui_views.return_value=(1,2,3,(10,11))
    else:
        original=r.ui.payload;calls=[]
        def payload(panel,name,*args):
            if name=='_cookerNetId':
                calls.append(1)
                return struct.pack('<I',len(calls))
            return original(panel,name,*args)
        r.ui.payload=payload
    with pytest.raises(ValueError):r.snapshot()
