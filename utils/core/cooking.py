"""Conservative single-stove state machine; observations never inject input."""
from dataclasses import dataclass, field
from collections import Counter
from uuid import uuid4

from utils.core import thresholds as policy


@dataclass(frozen=True)
class Recipe:
    name: str
    outputs: tuple
    ingredients: dict
    reserves: dict = field(default_factory=dict)
    default_outputs: bool = False
    recipe_id: int | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 64:
            raise ValueError('料理名称无效')
        if (not self.outputs and not self.default_outputs) or len(set(self.outputs)) != len(self.outputs) or any(
                type(i) is not int or i <= 0 for i in self.outputs):
            raise ValueError('料理产物编号尚未核对')
        if not self.ingredients or any(type(i) is not int or i <= 0 or type(n) is not int
                or not 1 <= n <= 1000 for i, n in self.ingredients.items()):
            raise ValueError('料理用料尚未核对')
        if any(i not in self.ingredients or type(n) is not int or n < 0 for i, n in self.reserves.items()):
            raise ValueError('材料保留数量无效')
        if set(self.outputs) & set(self.ingredients):
            raise ValueError('首版不支持原料与产物编号重合的料理')


@dataclass(frozen=True)
class Observation:
    stage: str = 'unknown'
    target: tuple | None = None
    frame_id: object = None
    key: str | None = None
    recipe_view: dict | None = None


def bind_default_recipe(view, inventory, reserve, max_star):
    """Validate the actual default filled stacks, not a guessed material plan."""
    if not isinstance(view, dict) or not view.get('scope'):
        raise ValueError('默认食谱归属尚未确认')
    selected = view.get('selected')
    if not isinstance(selected, dict) or type(selected.get('recipe_id')) is not int or selected['recipe_id'] <= 0:
        raise ValueError('默认食谱尚未就绪')
    slots = selected.get('slots')
    if not isinstance(slots, list) or not 1 <= len(slots) <= policy.COOKING_MAX_MATERIAL_SLOTS:
        raise ValueError('默认食谱材料槽尚未确认')
    rows = inventory.get('items')
    if not isinstance(rows, list):
        raise ValueError('默认材料的背包份数尚未确认')
    owned = {}
    for row in rows:
        net = row.get('net_id')
        if type(net) is int and net > 0:
            if net in owned:
                raise ValueError('背包材料身份存在歧义')
            owned[net] = row
    ingredients, stacks = Counter(), Counter()
    for slot in slots:
        if slot.get('filled') is not True:
            raise ValueError('默认食谱材料未填满，未投料')
        net, identity = slot.get('net_id'), slot.get('item_id')
        row = owned.get(net)
        if row is None or type(identity) is not int or row.get('item_id') != identity:
            raise ValueError('默认填料不属于当前随身背包')
        if row.get('locked') is not False or row.get('disabled') is not False:
            raise ValueError('默认填料含锁定或禁用物品，未投料')
        if type(row.get('star')) is not int or not 0 <= row['star'] <= max_star:
            raise ValueError('默认填料超过允许星级，未投料')
        ingredients[identity] += 1
        stacks[net] += 1
        if type(row.get('count')) is not int or row['count'] < stacks[net]:
            raise ValueError('默认填料实际份数不足，未投料')
    return Recipe(selected.get('name', ''), (), dict(ingredients),
                  {i: reserve for i in ingredients}, default_outputs=True, recipe_id=selected['recipe_id'])


def inventory_valid(snapshot):
    return (isinstance(snapshot, dict) and isinstance(snapshot.get('scope'), str)
            and bool(snapshot['scope']) and isinstance(snapshot.get('counts'), dict)
            and all(type(k) is int and k > 0 and type(v) is int and 0 <= v <= 100000000
                    for k, v in snapshot['counts'].items()))


class CookingSession:
    """Only a resolved pot may be followed by another material-consuming start."""
    terminal = {'finished', 'invalid', 'insufficient', 'unconfirmed', 'failed'}

    def __init__(self, recipe, target, timeout=policy.COOKING_TASK_SECONDS, receipt_timeout=policy.COOKING_RECEIPT_SECONDS,
                 default_mode=False, material_reserve=0, max_star=1):
        if type(target) is not int or not 1 <= target <= 10000:
            raise ValueError('成功锅数须为1～10000的整数')
        if not 1 <= timeout <= 3600 or not 1 <= receipt_timeout <= timeout:
            raise ValueError('烹饪等待上限无效')
        self.recipe, self.target = recipe, target
        self.timeout, self.receipt_timeout = timeout, receipt_timeout
        self.started = self.confirmed = 0
        self.state, self.reason = 'ready', '等待核对选菜和背包'
        self.pot = None
        self.stir_armed = True
        self.last_now = None
        self.ready_at = None
        if recipe is None and not default_mode:
            raise ValueError('烹饪食谱无效')
        if type(material_reserve) is not int or not 0 <= material_reserve <= 100000000 or type(max_star) is not int or not 0 <= max_star <= 5:
            raise ValueError('原料保护设置无效')
        self.default_mode, self.material_reserve, self.max_star = default_mode, material_reserve, max_star
        self.initial_opened = False
        self.recipe_scope = None

    def update(self, observation, inventory, now):
        if self.state in self.terminal:
            return None
        if self.last_now is not None and now < self.last_now:
            self.state, self.reason = 'invalid', '烹饪单调时钟回退'
            return None
        self.last_now = now
        if self.ready_at is None:
            self.ready_at = now
        valid = inventory_valid(inventory)
        stage = observation.stage
        view = getattr(observation, 'recipe_view', None)
        if self.default_mode and view and view.get('scope') and self.recipe_scope and view['scope'] != self.recipe_scope:
            self.state, self.reason = 'invalid', '灶台或场景归属变化，已停止烹饪'
            return None
        pot = self.pot
        if pot is not None:
            if valid and inventory['scope'] != pot['scope']:
                self.state, self.reason = 'invalid', '场景或背包归属变化，旧锅证据失效'
                return None
            if pot['resolved']:
                if now-pot['confirmed_at'] > self.receipt_timeout:
                    self.state, self.reason = 'unconfirmed', '收菜后未能重新打开选菜，不重复交互'
                    return None
                if stage == 'menu':
                    self.pot = None
                    self.state = 'ready'
                elif stage == 'interact' and not pot.get('menu_requested'):
                    pot['menu_requested'] = True
                    self.reason = '本锅已确认，重新打开灶台选菜'
                    return 'interact'
                else:
                    self.reason = '本锅已确认，等待回到选菜界面'
                    return None
            else:
                elapsed = now-pot['started_at']
                if valid:
                    pot['inventory_last'] = dict(inventory['counts'])
                # A gain before any cooking/completion observation is not proof.
                if valid and pot['cooking_seen'] and stage in ('done', 'menu', 'interact') and (
                        not self.recipe.default_outputs or pot['collected_at'] is not None):
                    outputs = self.recipe.outputs
                    if self.recipe.default_outputs:
                        outputs = {r['item_id'] for r in inventory.get('items', [])
                                   if r.get('prepared') is True and type(r.get('item_id')) is int}
                    delta = {i: inventory['counts'].get(i, 0)-pot['inventory_before'].get(i, 0)
                             for i in outputs}
                    if self.recipe.default_outputs:
                        delta = {i: n for i, n in delta.items() if n > 0}
                    if sum(delta.values()) > 0 and all(v >= 0 for v in delta.values()):
                        pot.update(resolved=True, confirmed=True, inventory_delta=delta,
                                   inventory_after=dict(inventory['counts']), confirmed_at=now)
                        self.confirmed += 1
                        self.state = 'finished' if self.confirmed >= self.target else 'ready'
                        self.reason = f'背包增量确认成功 {self.confirmed}/{self.target} 锅'
                        return None
                if stage == 'failed':
                    self.state, self.reason = 'failed', '识别到失败提示，本锅停止并保留证据'
                    return None
                if elapsed > self.timeout or pot['collected_at'] is not None and now-pot['collected_at'] > self.receipt_timeout:
                    self.state, self.reason = 'unconfirmed', '本锅结果未确认，不重新投料'
                    return None
                if stage in ('cooking', 'stir', 'done'):
                    pot['cooking_seen'] = True
                if stage == 'done':
                    self.state, self.reason = 'receipt', '等待成品入包确认'
                    if pot['collected_at'] is None:
                        pot['collected_at'] = now  # Reserve action before dispatch.
                        return 'collect'
                    return None
                if pot['collected_at'] is not None:
                    self.reason = '已尝试收菜，持续核对背包，不开启下一锅'
                    return None
                if stage == 'stir' and self.stir_armed:
                    self.stir_armed = False
                    self.state, self.reason = 'cooking', '响应翻炒提示'
                    pot['stirs'] += 1
                    return 'stir'
                if stage == 'cooking':
                    self.stir_armed = True
                    self.state, self.reason = 'cooking', '烹饪中，等待操作提示'
                elif stage == 'menu':
                    self.reason = '开始操作待确认，未重复投料'
                return None
        if self.default_mode and self.pot is None and stage == 'interact' and not self.initial_opened:
            self.initial_opened = True
            self.reason = '正在打开灶台，使用默认食谱'
            return 'interact'
        if stage != 'menu' or not valid:
            if self.pot is None and now-self.ready_at > self.timeout:
                self.state, self.reason = 'unconfirmed', '选菜或背包长时间未确认，未投料'
                return None
            self.reason = '正在识别灶台默认食谱与背包' if self.default_mode else '请先选好料理；等待稳定的选菜界面与背包'
            return None
        if self.default_mode:
            try:
                selected = bind_default_recipe(view, inventory, self.material_reserve, self.max_star)
            except ValueError as exc:
                self.state, self.reason = 'unconfirmed', str(exc)
                return None
            if self.recipe is not None and self.recipe.recipe_id != selected.recipe_id:
                self.state, self.reason = 'invalid', '本轮默认食谱已改变，未自动切换料理'
                return None
            self.recipe, self.recipe_scope = selected, view['scope']
        usable = inventory.get('usable_counts', inventory['counts'])
        if self.default_mode:
            usable = inventory.get('selectable_counts', usable)
        if not isinstance(usable, dict) or any(type(v) is not int or v < 0 for v in usable.values()):
            self.reason = '可投放原料份数未确认'
            return None
        if any(usable.get(i, 0)-n < self.recipe.reserves.get(i, 0)
               for i, n in self.recipe.ingredients.items()):
            self.state, self.reason = 'insufficient', '可用材料不足或达到保留量，未开始新锅'
            return None
        self.pot = {'id': uuid4().hex, 'scope': inventory['scope'], 'started_at': now,
                    'inventory_before': dict(inventory['counts']), 'inventory_last': dict(inventory['counts']),
                    'cooking_seen': False, 'collected_at': None, 'resolved': False, 'confirmed': False,
                    'stirs': 0, 'recipe': self.recipe.name, 'recipe_id': self.recipe.recipe_id,
                    'ingredients': dict(self.recipe.ingredients)}
        self.started += 1
        self.stir_armed = True
        self.state, self.reason = 'starting', '已保留开始操作，等待开锅确认'
        return 'start'

    def record(self):
        return {'cooking_state': self.state, 'reason': self.reason, 'started': self.started,
                'confirmed': self.confirmed, 'target': self.target, 'pot': self.pot}
