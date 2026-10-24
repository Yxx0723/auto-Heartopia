"""Conservative single-stove state machine; observations never inject input."""
from dataclasses import dataclass, field
from uuid import uuid4

from utils.core import thresholds as policy


@dataclass(frozen=True)
class Recipe:
    name: str
    outputs: tuple
    ingredients: dict
    reserves: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 64:
            raise ValueError('料理名称无效')
        if not self.outputs or len(set(self.outputs)) != len(self.outputs) or any(
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


def inventory_valid(snapshot):
    return (isinstance(snapshot, dict) and isinstance(snapshot.get('scope'), str)
            and bool(snapshot['scope']) and isinstance(snapshot.get('counts'), dict)
            and all(type(k) is int and k > 0 and type(v) is int and 0 <= v <= 100000000
                    for k, v in snapshot['counts'].items()))


class CookingSession:
    """Only a resolved pot may be followed by another material-consuming start."""
    terminal = {'finished', 'invalid', 'insufficient', 'unconfirmed', 'failed'}

    def __init__(self, recipe, target, timeout=policy.COOKING_TASK_SECONDS, receipt_timeout=policy.COOKING_RECEIPT_SECONDS):
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
                if valid and pot['cooking_seen'] and stage in ('done', 'menu', 'interact'):
                    delta = {i: inventory['counts'].get(i, 0)-pot['inventory_before'].get(i, 0)
                             for i in self.recipe.outputs}
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
        if stage != 'menu' or not valid:
            if self.pot is None and now-self.ready_at > self.timeout:
                self.state, self.reason = 'unconfirmed', '选菜或背包长时间未确认，未投料'
                return None
            self.reason = '请先选好料理；等待稳定的选菜界面与背包'
            return None
        usable = inventory.get('usable_counts', inventory['counts'])
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
                    'stirs': 0, 'recipe': self.recipe.name}
        self.started += 1
        self.stir_armed = True
        self.state, self.reason = 'starting', '已保留开始操作，等待开锅确认'
        return 'start'

    def record(self):
        return {'cooking_state': self.state, 'reason': self.reason, 'started': self.started,
                'confirmed': self.confirmed, 'target': self.target, 'pot': self.pot}
