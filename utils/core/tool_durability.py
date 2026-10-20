"""Validated durability display for the game's shared chopping/mining tool."""
from utils.core import thresholds as policy


def axe_pick_status(current, maximum, unlocked, equipped):
    return tool_status('斧镐', current, maximum, unlocked, equipped)


def tool_status(name, current, maximum, unlocked, equipped):
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError('工具名称无效')
    if type(unlocked) is not bool or type(equipped) is not bool:
        raise ValueError('工具状态类型无效')
    if not unlocked:
        return {'state': 'locked', 'name': name, 'message': f'{name}尚未解锁'}
    if type(current) is not int or type(maximum) is not int \
            or not 0 <= current <= maximum <= 10000000 or maximum == 0:
        raise ValueError('工具耐久数值无效')
    percent = current/maximum*100
    condition = 'broken' if current == 0 else 'low' if percent <= policy.COORD_TOOL_LOW_PERCENT else 'normal'
    return {'state': 'ready', 'name': name, 'current': current, 'maximum': maximum,
            'percent': percent, 'condition': condition, 'equipped': equipped}
