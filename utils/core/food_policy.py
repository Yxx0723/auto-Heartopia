"""Confirmed inventory policy; unknown is never equivalent to no prepared dishes."""
FRUIT_NAMES = {'苹果', '蓝莓', '树莓', '橘子'}
FALLBACK_MODES = ('fruit', 'fruit_mushroom', 'all')


def choose_foods(inventory, fallback_mode='fruit'):
    if fallback_mode not in FALLBACK_MODES:
        raise ValueError('无料理时的食物范围无效')
    def result(state, items=(), star=None, reason=''):
        return dict(state=state, items=list(items), star=star, reason=reason)
    if not isinstance(inventory,list):
        return result('unknown',reason='背包库存未确认，稍后自动重试')
    available=[]
    for item in inventory:
        if not isinstance(item,dict):return result('unknown',reason='背包物品记录未确认')
        count=item.get('count')
        if type(count) is not int or count<0:return result('unknown',reason='背包物品数量未确认')
        if 'entity_type' in item and type(item['entity_type']) is not int:
            return result('unknown',reason='食物实际类型未确认')
        if not count or item.get('edible') is not True or item.get('locked') or item.get('disabled'):continue
        available.append(item)
    def prepared(item):
        # Native snapshots always provide entity_type. The boolean is retained
        # only for older injected/offline adapters, not a name-based classifier.
        return item.get('entity_type')==45 if 'entity_type' in item else item.get('prepared') is True
    dishes=[item for item in available if prepared(item)]
    if dishes:
        if any(type(item.get('star')) is not int or not 0<=item['star']<=5 for item in dishes):
            return result('unknown',reason='成品料理星级未确认，稍后自动重试')
        star=min(item['star'] for item in dishes)
        tier=[item for item in dishes if item['star']==star]
        pool=[item for item in tier if not item.get('ambiguous_locked_variant')]
        if not pool:return result('unknown',reason='最低星级料理存在同名同星锁定份数，选中身份未确认')
        state='prepared'
    else:
        def allowed(item):
            kind=item.get('entity_type')
            return (fallback_mode=='all' or kind==25 or item.get('name') in FRUIT_NAMES
                    or fallback_mode=='fruit_mushroom' and kind==97)
        raw=[item for item in available if allowed(item)]
        pool=[item for item in raw if not item.get('ambiguous_locked_variant')]
        if not pool:
            return result('unknown' if raw else 'unavailable',reason=(
                '可食用份数的锁定身份未确认' if raw else '随身背包内没有确认可食用的食物'))
        state,star='fallback',None
    if any(not isinstance(i.get('name'),str) or not 1<=len(i['name'])<=32
           or any(ord(c)<32 for c in i['name']) for i in pool):
        return result('unknown',reason='目标食物名称未确认，稍后自动重试')
    return result(state,pool,star)


def confirm_inventory(target, inventory, fallback_mode='fruit'):
    pool=choose_foods(inventory,fallback_mode)
    if pool['state'] not in ('prepared','fallback'):
        raise ValueError(pool['reason'])
    if (pool['state']=='prepared') != target.get('prepared',False):
        raise ValueError('料理库存优先级已变化，重新选择食物')
    matches=[item for item in pool['items'] if item['name']==target.get('food')]
    if not matches or pool['state']=='prepared' and pool['star']!=target.get('star'):
        raise ValueError('目标份数或最低星级已变化，重新选择食物')
    if target.get('requires_star') and not any(i.get('star')==target.get('star') for i in matches):
        raise ValueError('目标同名星级存在歧义，未食用')
    return pool
