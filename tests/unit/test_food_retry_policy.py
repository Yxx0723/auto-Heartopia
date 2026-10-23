import pytest
from utils.core.food_policy import choose_foods


def dish(star, **extra):
    return dict(name='料理'+str(star), count=2, edible=True, entity_type=45, star=star, **extra)


def test_unknown_inventory_is_not_empty_inventory():
    assert choose_foods(None)['state']=='unknown'
    assert choose_foods([])['state']=='unavailable'


def test_actual_type_and_lowest_star_define_pool():
    pool=choose_foods([dish(2),dish(0),dish(1),dict(name='苹果',count=3,edible=True,entity_type=25)])
    assert pool['state']=='prepared' and pool['star']==0
    assert [x['name'] for x in pool['items']]==['料理0']


def test_unknown_or_ambiguous_low_star_does_not_upgrade_or_fall_back():
    assert choose_foods([dish(0,ambiguous_locked_variant=True),dish(1)])['state']=='unknown'
    assert choose_foods([dish(None),dish(1)])['state']=='unknown'


def test_only_unavailable_dishes_allow_configured_fallback():
    items=[dish(0,locked=True),dict(name='苹果',count=2,edible=True,entity_type=25),
           dict(name='平菇',count=2,edible=True,entity_type=97)]
    assert [x['name'] for x in choose_foods(items)['items']]==['苹果']
    assert {x['name'] for x in choose_foods(items,'fruit_mushroom')['items']}=={'苹果','平菇'}


def test_inventory_errors_retry_without_pausing_or_consuming():
    from tests.unit.test_coordinate_auto_food import HungryGame, Fruit, run
    game=HungryGame(16);food=Fruit(game);calls=[]
    original=food.prepare
    def prepare(*args):
        calls.append(game.time)
        if len(calls)<=2:raise ValueError('search not confirmed')
        return original(*args)
    food.prepare=prepare
    result,food,status=run(game,food)
    assert result and game.energy==100 and len(calls)>=3
    assert calls[1]-calls[0]>=1.
    assert not any(s=='food_paused' for s,_,_ in status)


def test_unconfirmed_previous_use_does_not_consume_again():
    from tests.unit.test_coordinate_auto_food import HungryGame, run
    game=HungryGame(16);game.food_effect=False;game.stop_at=25.
    result,food,status=run(game)
    assert not result and len(food.uses)==1 and not game.keys
    assert not any(s=='food_paused' for s,_,_ in status)


def test_available_low_star_search_failure_never_becomes_raw_fallback():
    pool=choose_foods([dish(0),dish(3),dict(name='苹果',count=2,edible=True,entity_type=25)])
    assert pool['state']=='prepared' and pool['star']==0
    assert all(i['entity_type']==45 for i in pool['items'])


def test_every_bite_refetches_and_only_promotes_after_lower_tier_depleted():
    from tests.unit.test_coordinate_auto_food import HungryGame,Fruit,run
    game=HungryGame(16,restore=15);food=Fruit(game);chosen=[]
    def inventory(check):
        check();used=len(food.uses)
        return [dict(name='0星料理',star=0,entity_type=45,edible=True,count=max(0,2-used)),
                dict(name='1星料理',star=1,entity_type=45,edible=True,count=1 if used<3 else 0),
                dict(name='2星料理',star=2,entity_type=45,edible=True,count=1 if used<4 else 0),
                dict(name='苹果',entity_type=25,edible=True,count=8)]
    def prepare(window,tap,wait,check,device,inventory,query_check,fallback_mode):
        pool=choose_foods(inventory,fallback_mode);item=pool['items'][0]
        chosen.append(item['name'])
        return dict(food=item['name'],prepared=pool['state']=='prepared',star=pool['star'],requires_star=pool['state']=='prepared')
    food.prepare=prepare
    food.confirm=lambda target,*a,**kw:target
    game.food_inventory_snapshot=inventory
    result,food,statuses=run(game,food)
    assert result and game.energy==100
    assert chosen==['0星料理','0星料理','1星料理','2星料理','苹果','苹果']


def test_search_query_changing_between_frames_rejects_button():
    from tests.unit.test_food_search import search_game
    from unittest.mock import Mock
    menu,window,device,state=search_game()
    state.update(page='results',query='苹果',selected=True)
    reads=iter(['苹果','蓝莓'])
    target=dict(food='苹果',cell={'x':4,'y':4},prepared=False,requires_star=False)
    with pytest.raises(ValueError,match='搜索文字不符'):
        menu.confirm(target,window,Mock(),Mock(),query_check=lambda:next(reads))
    assert not state['eaten']


def test_new_low_star_inventory_blocks_previously_selected_high_star():
    from utils.core.food_policy import confirm_inventory
    with pytest.raises(ValueError,match='最低星级'):
        confirm_inventory(dict(food='料理1',prepared=True,star=1),[dish(0),dish(1)])


def test_stop_during_retry_delay_is_responsive_and_releases_keys():
    from tests.unit.test_coordinate_auto_food import HungryGame,Fruit,run
    game=HungryGame(16);game.stop_at=.35;food=Fruit(game)
    def missing(*args):raise ValueError('search unavailable')
    food.prepare=missing
    result,food,_=run(game,food)
    assert not result and not food.uses and not game.keys
    assert game.time<=.4


def test_pause_during_retry_then_resume_does_not_disable_automatic_refill():
    from tests.unit.test_coordinate_auto_food import HungryGame,Fruit,run
    game=HungryGame(16);game.pause_at=.15;game.resume_at=1.4;food=Fruit(game)
    original=food.prepare;calls=[]
    def prepare(*args):
        calls.append(game.time)
        if len(calls)==1:raise ValueError('search unavailable')
        return original(*args)
    food.prepare=prepare
    result,food,_=run(game,food)
    assert result and game.energy==100 and len(food.uses)==9


def test_selected_cell_losing_its_outline_between_frames_cannot_return_eat_button():
    from tests.unit.test_food_search import search_game
    from unittest.mock import Mock
    menu,window,device,state=search_game()
    state.update(page='results',query='苹果',selected=True)
    pictures=[dict(state),dict(state,selected=False)]
    window.snapshot.side_effect=lambda:pictures.pop(0)
    with pytest.raises(ValueError,match='详情'):
        menu.confirm(dict(food='苹果',cell={'x':4,'y':4},requires_star=False),window,Mock(),Mock(),query_check=lambda:'苹果')
    assert not state['eaten']


def test_lower_star_on_next_results_page_is_found_without_using_high_star():
    from tests.unit.test_food_search import search_game
    from unittest.mock import Mock
    menu,window,device,state=search_game(available=('温泉蛋',))
    class Frame(dict):shape=(437,778,3)
    menu.frame=lambda picture:Frame(picture)
    menu.bag_in=lambda p:dict(x=730,y=50,scale=1.,search_results=True) if p['page']=='results' else None
    click=menu.use
    def use(target,*args):
        if target.get('food')=='温泉蛋':state['selected']=True
        else:click(target,*args)
    menu.use=use
    menu.ready_food=lambda p,food,cell:dict(x=5,y=5,food=food) if p['selected'] else None
    menu.detail_stars=lambda p,button:0 if p.get('page_no')==1 else 3
    def execute(event):
        if event['type']=='scroll':state['page_no']=state.get('page_no',0)+1
    device.execute.side_effect=execute
    target=menu.prepare(window,Mock(),Mock(),Mock(),device,
        inventory=[dict(name='温泉蛋',count=1,edible=True,entity_type=45,star=0)])
    assert target['star']==0 and state['page_no']==1
    assert not state['eaten']
