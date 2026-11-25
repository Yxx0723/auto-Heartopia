"""Readable fishing records shared by the window and the file log."""
from datetime import datetime

EVENT_TYPES = {
    'button': ('输入', '已派发'), 'move': ('输入', '已派发'), 'hotkey': ('输入', '已派发'),
    'fish_detected': ('识别', '已确认'), 'aim_observe': ('识别', '进行中'),
    'game_state': ('游戏反馈', '已确认'), 'retrieved': ('游戏反馈', '已确认'),
    'hook': ('游戏反馈', '已确认'), 'land': ('游戏反馈', '未确认'),
    'line_sample': ('张力', '进行中'), 'catch': ('钓获', '已确认'),
    'escape': ('逃脱', '已确认'), 'cast_effective': ('抛竿反馈', '已确认'),
    'result_unknown': ('结果', '未确认'), 'cast_miss': ('游戏反馈', '未确认'), 'cast': ('抛竿', '已派发'),
    'slack': ('张力', '进行中'), 'rehold': ('张力', '进行中'),
    'abort_aim': ('提醒', '未确认'), 'interrupt': ('停止', '未确认'),
    'end': ('结束', '已完成'), 'error': ('失败', '失败'),
}


def format_fishing_event(row):
    label, style = EVENT_TYPES.get(row['ev'], ('状态', '进行中'))
    return f"{row['at']} [{label}] {row['reason']}\n", style


def append_fishing_event(path, row):
    line, _ = format_fishing_event(row)
    try:
        with path.open('a', encoding='utf-8-sig') as stream:
            stream.write(line)
    except OSError:
        pass


def fishing_event(event, reason, **extra):
    return {'at': datetime.now().strftime('%H:%M:%S'), 'ev': event, 'reason': reason, **extra}
