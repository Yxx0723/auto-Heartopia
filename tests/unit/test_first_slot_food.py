from unittest.mock import Mock
import pytest
from utils.vision.first_slot_food import FirstSlotFoodMenu
from tests.unit.test_coordinate_auto_food import HungryGame,Fruit,run


def menu():
    m=FirstSlotFoodMenu.__new__(FirstSlotFoodMenu)
    m.frame=lambda im:im;m.search_dialog=Mock(return_value=None)
    m.end_search_target=Mock(return_value=None)
    return m


def test_open_b_then_only_first_slot_without_search_or_star_reads():
    m=menu();opened=[False];window=Mock();window.snapshot.side_effect=lambda:{'open':opened[0]}
    m.locate=lambda im:{'x':730,'y':50,'scale':1.} if im['open'] else None
    keys=[]
    def tap(key):keys.append(key);opened[0]=True
    m.use=Mock();m.ready_first=Mock(return_value={'food':'背包第一格','x':200,'y':100})
    result=m.prepare(window,tap,lambda _:None,lambda:None,Mock())
    assert keys==['b'] and m.use.call_count==1
    cell=m.use.call_args.args[0]
    assert cell['x']==519 and cell['y']==96
    assert result['food']=='背包第一格'


def test_no_eat_action_never_advances_to_second_slot():
    m=menu();window=Mock();m.locate=Mock(return_value={'x':730,'y':50,'scale':1.})
    m.use=Mock();m.ready_first=Mock(return_value=None)
    with pytest.raises(ValueError,match='不会点击其他格子'):
        m.prepare(window,Mock(),lambda _:None,lambda:None,Mock())
    assert m.use.call_count==1


def test_first_slot_refills_and_resumes_without_inventory_or_query():
    class Food(Fruit):
        direct_first_slot=True
        def confirm(self,target,window,wait,check):check();return target
    g=HungryGame(29,restore=40);food=Food(g)
    g.food_inventory_snapshot=Mock(side_effect=AssertionError('No inventory priority'))
    g.food_search_query=Mock(side_effect=AssertionError('No search'))
    result,food,_=run(g,food)
    assert result and g.energy==100 and len(food.uses)==2 and food.closes==1
    g.food_inventory_snapshot.assert_not_called();g.food_search_query.assert_not_called()
