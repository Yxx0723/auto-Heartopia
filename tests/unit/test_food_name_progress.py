"""An unresolved name must not hide usable stock or change its priority."""
import struct
import pytest
from utils.core.food_policy import choose_foods
from utils.drivers import food_inventory
from tests.unit.test_food_inventory import inventory


def pending():
    cls=getattr(food_inventory,'FoodNamesPending',ValueError)
    return cls('正在核对新食物名称，稍后自动重试')


def unnamed(star=1,kind=45,**extra):
    return dict(item_id=45101,name=None,count=7,edible=True,entity_type=kind,star=star,**extra)


def egg(star=1):
    return dict(item_id=45420,name='温泉蛋',count=7,edible=True,entity_type=45,star=star)


@pytest.mark.parametrize('kind,star',[(45,1),(45,2),(97,0),(25,0)])
def test_unresolved_other_names_do_not_block_a_confirmed_lowest_tier(kind,star):
    pool=choose_foods([unnamed(star,kind),egg(),egg(2)])
    assert pool['state']=='prepared' and pool['star']==1
    assert [i['name'] for i in pool['items']]==['温泉蛋']


def test_unknown_lower_tier_cannot_be_skipped_for_higher_tier_or_fruit():
    pool=choose_foods([unnamed(0),egg(),dict(name='苹果',count=20,edible=True,entity_type=25)])
    assert pool['state']=='unknown' and not pool['items']


def test_unknown_fruit_does_not_block_other_known_fruit_when_no_dishes():
    pool=choose_foods([unnamed(0,25),dict(name='苹果',count=20,edible=True,entity_type=25)])
    assert pool['state']=='fallback' and [i['name'] for i in pool['items']]==['苹果']


def test_unknown_dish_is_not_an_empty_prepared_inventory():
    assert choose_foods([unnamed(),dict(name='苹果',count=20,edible=True,entity_type=25)])['state']=='unknown'


def test_inventory_keeps_unresolved_food_metadata_and_checks_ownership():
    r,_=inventory();rows,stable=r.dictionary_rows.return_value;ci,old=rows[-1]
    data=bytearray(old);struct.pack_into('<i',data,0,45101)
    r.dictionary_rows.return_value=([*rows,(ci,bytes(data))],stable)
    r.discover_names.side_effect=lambda *args:(_ for _ in ()).throw(pending())
    foods=r.snapshot()
    assert next(f for f in foods if f['item_id']==45101)['name'] is None
    assert next(f for f in foods if f['item_id']==45420)['name']=='温泉蛋'
    r.stable.assert_called_once()


def test_partial_name_lookup_never_hides_a_backpack_change():
    r,_=inventory();r.discover_names.side_effect=lambda *args:(_ for _ in ()).throw(pending())
    r.stable.return_value=False
    with pytest.raises(ValueError,match='发生变化'):r.snapshot()


def test_name_table_or_domain_errors_are_not_treated_as_partial_success():
    r,_=inventory();r.discover_names.side_effect=ValueError('食物名称表尚未就绪')
    with pytest.raises(ValueError,match='名称表'):r.snapshot()


def test_each_bite_uses_the_known_lowest_tier_and_still_refills_to_100():
    from tests.unit.test_coordinate_auto_food import HungryGame,Fruit,run
    g=HungryGame(25,restore=30);food=Fruit(g);chosen=[]
    g.food_inventory_snapshot=lambda check:[unnamed(),egg(),egg(2)]
    def prepare(window,tap,wait,check,device,inventory,query_check,fallback_mode):
        pool=choose_foods(inventory,fallback_mode)
        if pool['state']!='prepared':raise ValueError(pool['reason'])
        chosen.append((pool['items'][0]['name'],pool['star']))
        return dict(food=pool['items'][0]['name'],prepared=True,star=pool['star'],requires_star=True)
    food.prepare=prepare;food.confirm=lambda target,*a,**kw:target
    result,food,_=run(g,food)
    assert result and g.energy==100 and chosen and all(v==('温泉蛋',1) for v in chosen)
