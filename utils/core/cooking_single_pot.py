"""Single-pot material allocation and event acknowledgement; no input or vision."""
from collections import Counter

from utils.core.cooking_catalog import base_recipe

# Read from this client's EcsClient CookingStatus literal constants, not timing.
STATUSES = {0: 'Idle', 1: 'Preparing', 2: 'Cooking', 3: 'Danger',
            4: 'Relief', 5: 'Succeed', 6: 'Failed'}


def material_plan(recipe, view, inventory):
    card = base_recipe(recipe)
    selected = view.get('selected') or {}
    if (selected.get('recipe_id'), selected.get('name')) != (card['recipe_id'], card['name']):
        raise ValueError('实际料理与本次选择不一致，未投料')
    slots = view['slots']
    requirements = {('group' if r['item_id'] is None else 'item',
                     r['material_group_id'] if r['item_id'] is None else r['item_id']): r
                    for r in card['ingredients']}
    expected = Counter({k: r['quantity'] for k, r in requirements.items()})
    actual = Counter((s['kind'], s['required_id']) for s in slots)
    if actual != expected:
        raise ValueError('游戏材料槽需求与离线配方不一致，未投料')
    if card is not recipe:
        variant_requirements = {}
        for material in recipe['ingredients']:
            allowed = set(material['allowed_item_ids'])
            matches = [key for key, base in requirements.items()
                       if allowed and allowed <= set(base['allowed_item_ids'])
                       and material['quantity'] == base['quantity']]
            if len(matches) != 1 or matches[0] in variant_requirements:
                raise ValueError('变种材料无法唯一对应基础食谱槽位，未投料')
            variant_requirements[matches[0]] = material
        if variant_requirements.keys() != requirements.keys():
            raise ValueError('变种配方未覆盖全部基础食谱槽位，未投料')
        requirements = variant_requirements
    rows = {r['net_id']: r for r in inventory['items']}
    if len(rows) != len(inventory['items']):
        raise ValueError('背包材料分堆身份不唯一')
    remaining = {n: r['count'] for n, r in rows.items()
                 if not r['locked'] and not r['disabled'] and 0 <= r['star'] <= 5}
    choices = []
    for slot in slots:
        allowed = set(requirements[(slot['kind'], slot['required_id'])]['allowed_item_ids'])
        candidates = [r for n, r in rows.items() if remaining.get(n, 0) and r['item_id'] in allowed]
        candidates.sort(key=lambda r: (r['net_id'] != slot['net_id'], r['star'], r['net_id']))
        choices.append(candidates)
    ordered = sorted(range(len(slots)), key=lambda i: len(choices[i])); plan = [None]*len(slots)
    def assign(step):
        if step == len(ordered):
            return True
        i = ordered[step]
        for row in choices[i]:
            net = row['net_id']
            if remaining[net] <= 0:
                continue
            remaining[net] -= 1
            plan[i] = {'index': i, 'net_id': net, 'item_id': row['item_id']}
            if assign(step+1):
                return True
            remaining[net] += 1
        return False
    if not assign(0):
        raise ValueError('背包可用材料无法满足全部槽位，未投料')
    return plan


def slots_match(slots, plan):
    return len(slots) == len(plan) and all(s['filled'] and
        (s['net_id'], s['item_id']) == (p['net_id'], p['item_id']) for s, p in zip(slots, plan))


class SinglePotEvents:
    """One start, one collect, and one dispatch per actual Danger phase."""
    def __init__(self, recipe):
        self.recipe = recipe
        self.started = False
        self.cooking_seen = False
        self.completed = False
        self.collected = False
        self.pending = None
        self.events = {}
        self.last_phase = None

    def observe(self, state):
        status = state['status']
        if status not in STATUSES:
            raise ValueError('未知烹饪状态，未执行交互')
        if status and state['owner_net_id'] != state['local_net_id']:
            raise ValueError('锅次不属于当前人物')
        if status in (2, 3, 4, 5) and state['food_id'] != self.recipe['product_id']:
            raise ValueError('实际锅内产物与指定料理不一致')
        if status == 6:
            raise ValueError('游戏确认烹饪失败，未将其登记为成功')
        previous_phase=self.last_phase
        phase=(status,state['status_started'])
        self.last_phase=phase
        if not self.started:
            if status != 0:
                raise ValueError('灶台已有未处理锅次，不能启动新锅')
            return None
        if status in (1, 2, 3, 4):
            self.cooking_seen = True
        had_pending=self.pending is not None
        if status==4 and not had_pending and previous_phase!=phase:
            raise ValueError('观测到未处理的翻锅解除阶段，不能掩盖遗漏事件')
        if status in (2,5) and state['previous_status']==4 and not had_pending \
                and previous_phase!=phase and (previous_phase is None or previous_phase[0]!=4):
            raise ValueError('两次采样之间存在未处理的翻锅事件，未登记成功')
        if self.pending and status != 3:
            event = self.events[self.pending]
            # Relief is the game's acknowledgement. A disappearing icon or
            # elapsed countdown alone does not acknowledge the action.
            if not event['confirmed']:
                if event['dispatched'] and (status == 4 or state['previous_status'] == 4):
                    event['confirmed'] = True
                else:
                    raise ValueError('翻锅事件已结束但未取得解除确认，本锅结果未确认')
            self.pending = None
        if status == 3:
            if state['status_started'] <= 0:
                raise ValueError('翻锅事件身份未确认')
            key = str(state['status_started'])
            if self.pending and self.pending != key:
                raise ValueError('上一翻锅事件尚未确认，不能掩盖遗漏事件')
            if key not in self.events:
                self.events[key] = {'dispatched': False, 'confirmed': False}
            elif self.events[key]['confirmed']:
                raise ValueError('已确认的旧翻锅事件重新出现，未重复派发')
            self.pending = key
            return None if self.events[key]['dispatched'] else ('stir', key)
        if state['previous_status'] == 3 and status not in (3, 4):
            raise ValueError('检测到未观测处理的翻锅阶段，未登记为成功')
        if status == 5:
            if not self.cooking_seen or any(not e['confirmed'] for e in self.events.values()):
                raise ValueError('烹饪过程或翻锅确认不完整，未收菜')
            self.completed = True
            return None if self.collected else ('collect', str(state['status_started']))
        return None

    def reserve(self, action, key=None):
        if action == 'start':
            if self.started:
                raise ValueError('禁止重复启动本锅')
            self.started = True
        elif action == 'stir':
            event = self.events[key]
            if event['dispatched']:
                raise ValueError('禁止重复处理同一翻锅事件')
            event['dispatched'] = True
        elif action == 'collect':
            if not self.completed or self.collected:
                raise ValueError('收菜状态未经确认或已经派发')
            self.collected = True
        else:
            raise ValueError('未知烹饪操作')

    def record(self):
        return {'start_reserved': self.started, 'cooking_seen': self.cooking_seen,
                'succeed_seen': self.completed, 'collect_reserved': self.collected,
                'pending_event': self.pending, 'events': self.events}
