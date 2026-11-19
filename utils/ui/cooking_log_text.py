"""中文显示文案：把烹饪流程里的内部步骤 / 状态代号翻成人能看懂的说法。

这里只负责显示。事件文件（events.jsonl）与 batch.json 里仍然写原始代号，
排查问题时照旧可查；界面上未收录的代号会原样显示，新步骤不会被悄悄吞掉。
"""
import re

# utils/flows/cooking_batch.py 里 step() 使用的步骤代号。
STEP_TEXT = {
    'create_pot_reader': '准备读取灶台',
    'bind_previous_confirmed_stove': '核对上一锅的灶台',
    'confirm_idle_before_entry': '确认人物空闲（开锅前）',
    'read_material_inventory': '读取背包材料',
    'open_recipe_panel': '打开食谱面板',
    'reread_recipe_panel': '复读食谱面板',
    'bind_open_panel_stove': '核对面板里的灶台',
    'confirm_idle_after_panel': '确认人物空闲（面板打开后）',
    'p1_select_recipe': '选择并确认料理',
    'revalidate_stove_and_scene': '复核灶台与场景',
    'read_inventory_before_p2': '记录开锅前成品数',
    'enter_p2': '进入自动开锅',
    'p2_single_pot': '自动开锅',
    'check_first_recipe_notice': '检查首次制作提示',
    'check_album_unlock': '检查图鉴弹窗',
    'dismiss_album_unlock': '关闭图鉴弹窗',
}

# 事件状态代号；用于日志每行的「发生了什么」。
STATE_TEXT = {
    'idle': '空闲',
    'selection_wait': '等待确认料理',
    'single_pot_wait': '等待按 F6 开始',
    'batch_wait': '等待按 F6 开始',
    'batch_preparing': '准备下一锅',
    'batch_step': '步骤',
    'batch_pot_finished': '本锅完成',
    'batch_finished': '全部完成',
    'batch_stopped': '已停止',
    'batch_first_recipe_notice': '首次制作提示',
    'album_unlock_notice': '图鉴解锁弹窗',
    'album_unlock_dismissed': '图鉴弹窗已关闭',
    'inventory_ready': '背包已读取',
    'inventory_error': '背包读取失败',
    'cooking_entry_wait': '等待食谱面板',
    'cooking_wait': '等待游戏响应',
    'cooking_action': '烹饪操作',
    'cooking': '烹饪状态',
    'preparing_materials': '核对用料',
    'recipe_confirming': '确认料理',
    'recipe_snapshot_changed': '食谱读取已变更',
    'recipe_snapshot_read_failed': '食谱读取失败',
    'recipe_snapshot_recovered': '食谱读取已恢复',
    'recipe_snapshot_retry_exhausted': '食谱读取已放弃',
    'recipe_read_retry': '重新读取料理控件',
    'recipe_read_recovered': '料理控件读取已恢复',
    'recipe_detail_sync_pending': '等待料理详情刷新',
    'recipe_detail_sync_recovered': '料理详情已刷新',
    'variant_recipe_readback': '变种料理选菜回读',
    'variant_recipe_dispatch': '变种料理卡片点击',
    'variant_recipe_confirmation_failed': '变种料理选菜未确认',
    'variant_material_candidate': '变种材料槽读取',
    'variant_slot_observation': '变种材料槽点击前读取',
    'variant_slot_replaced': '变种材料槽替换确认',
    'variant_start_comparison': '变种开锅按钮比较',
    'variant_start_stability_wait': '等待开始按钮稳定',
    'start_attempt': '尝试开锅',
    'start_confirmation': '开锅确认',
    'start_retry_blocked': '补点被拦截',
    'start_reserved': '开锅已登记',
    'stir_reserved': '翻锅已登记',
    'collect_reserved': '收菜已登记',
    'collect_key_dispatched': '已派发收菜键',
    'stir_alt_down': '按住翻锅键',
    'stir_alt_released': '松开翻锅键',
    'stir_retry': '翻锅重试',
    'mouse_diagnostic': '鼠标到位',
    'ack_post_diagnostic': '点击后复核',
    'start_ui_diagnostic': '开始按钮观察',
    'start_left_diagnostic': '开始左键派发',
    'stir_left_diagnostic': '翻锅左键派发',
    'native_observation': '游戏状态',
    'playback_wait': '等待开始',
    'playing': '开始烹饪',
    'material_unconfirmed': '材料未确认',
    'stock_check': '核对背包',
    'saved': '已记录',
    'finished': '已完成',
    'error': '失败',
    'stopped': '已停止',
    'paused': '已暂停',
    'blocked': '受阻',
    'unconfirmed': '未确认',
    'MENU_OPEN': '食谱面板已打开',
    'RECIPE_SELECTED': '料理已选中',
    'RECIPE_VALIDATED': '料理已核对',
    'ENTRY_DISPATCHED': '已派发按键',
}

# 日志行左侧方括号里的短标签；与采集日志的 [准备] [寻路] [采集] 保持同一读法。
PHASE_TEXT = {
    'create_pot_reader': '读取灶台',
    'bind_previous_confirmed_stove': '核对灶台',
    'confirm_idle_before_entry': '确认空闲',
    'read_material_inventory': '读取材料',
    'open_recipe_panel': '打开食谱',
    'reread_recipe_panel': '复读食谱',
    'bind_open_panel_stove': '核对灶台',
    'confirm_idle_after_panel': '确认空闲',
    'p1_select_recipe': '选择料理',
    'revalidate_stove_and_scene': '复核灶台',
    'read_inventory_before_p2': '记录成品',
    'enter_p2': '进入开锅',
    'p2_single_pot': '自动开锅',
    'check_first_recipe_notice': '首次提示',
    'check_album_unlock': '图鉴弹窗',
    'dismiss_album_unlock': '图鉴弹窗',
    'idle': '空闲',
    'batch_wait': '等待',
    'single_pot_wait': '等待',
    'selection_wait': '等待',
    'playback_wait': '等待',
    'playing': '运行',
    'batch_preparing': '准备',
    'batch_step': '步骤',
    'batch_pot_finished': '本锅完成',
    'batch_finished': '全部完成',
    'batch_stopped': '停止',
    'batch_first_recipe_notice': '首次提示',
    'finished': '完成',
    'stopped': '停止',
    'error': '失败',
    'paused': '暂停',
}

# 这些提示只是把事件名再说一遍，界面上不再重复显示。
DETAIL_ECHO = (
    '烹饪原始状态观测',
    '烹饪鼠标到位诊断',
    'UI 确认事后诊断',
    '开始按钮只读诊断',
)

# 开锅确认的内部代码；只影响显示。
CONFIRM_TEXT = {
    'CLICK_DISPATCHED': '已点击，等待游戏响应',
    'POLLING': '等待游戏确认',
    'OPEN_SUCCESS': '已开锅',
}

_REASON_PREFIXES = ('连续制作停止：', '自动烹饪停止：', '单锅停止：', '烹饪停止：')
_REASON_CLASS = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt):\s*')


def step_text(code):
    """批次步骤代号 → 中文；没收录的代号原样返回。"""
    if not code:
        return ''
    return STEP_TEXT.get(str(code), str(code))


def state_text(code):
    """事件状态代号 → 中文；没收录的代号原样返回。"""
    if not code:
        return ''
    return STATE_TEXT.get(str(code), str(code))


def stage_label(code, state):
    """日志里的「阶段」；与事件本身重复时留空，避免一行说两遍。"""
    if not code:
        return ''
    text = str(code)
    if text == str(state):
        return ''  # 没有更具体的步骤，事件名本身已经说明
    return STEP_TEXT.get(text) or STATE_TEXT.get(text) or text


def phase_text(code, state):
    """方括号里的短标签：优先当前步骤，其次事件本身；都没有时写「状态」。

    没收录的步骤代号原样返回，新步骤不会在日志里变成笼统的「步骤」。
    """
    text = str(code or '')
    if text and text != str(state or ''):
        return PHASE_TEXT.get(text) or step_text(text)
    text = text or str(state or '')
    # 代码与状态同名时先查事件状态表；直接走 step_text 会把已知状态
    # 当成「未收录的步骤代号」原样回显（例如 [inventory_ready]）。
    return PHASE_TEXT.get(text) or STATE_TEXT.get(text) or step_text(text) or '状态'


def batch_status_text(batch, result_state):
    """「批次状态：…」这一段的取值。"""
    state = (batch or {}).get('state') or result_state
    if state == 'batch_finished':
        return '已完成'
    if state == 'batch_stopped':
        return '已停止（失败）'
    if state == 'batch_wait' or result_state == 'batch_wait':
        return '等待开始'
    if state in (None, 'idle'):
        return '空闲'
    return '进行中'


def step_summary(batch, result_state):
    """「当前阶段：…」这一段的取值；没有具体步骤时退回事件状态。"""
    step = step_text(((batch or {}).get('diagnostic_context') or {}).get('current_step'))
    if step:
        return step
    state = (batch or {}).get('state') or result_state
    if state in (None, 'idle', 'batch_wait', 'batch_finished', 'batch_stopped'):
        return ''  # 批次状态那一半已经说过了
    return state_text(result_state)


def human_reason(text):
    """去掉「连续制作停止：」「ValueError:」这类前缀，只留给人看的原因。"""
    text = str(text or '').strip()
    for _ in range(4):
        cut = False
        for prefix in _REASON_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                cut = True
        match = _REASON_CLASS.match(text)
        if match:
            text = text[match.end():].strip()
            cut = True
        if not cut:
            break
    return text


def detail_text(state, result):
    """步骤回声、轮询回声不再重复一遍；真正的原因照原样显示。"""
    text = str(result or '').strip()
    if not text or state == 'batch_step':
        return ''
    if text.startswith(('执行批次步骤：', '完成批次步骤：', '读取实际烹饪状态：')):
        return ''
    if text in DETAIL_ECHO:
        return ''
    return human_reason(text).replace('程序持有的 ALT', ' ALT')


def log_line(row):
    """一行日志：时:分:秒 [阶段] 第 N 锅 · 事件：原因。

    与采集日志同一读法：时间在前、方括号写阶段、后面是一句人话。
    状态本身不再念一遍（颜色已经区分），只在原因里把话说清楚。
    """
    try:
        pot = int(row.get('pot'))
    except (TypeError, ValueError):
        pot = 0
    stamp = str(row.get('time') or '').strip() or '时间未提供'
    phase = str(row.get('phase') or row.get('stage_label') or '').strip() \
        or phase_text(row.get('stage'), row.get('state'))
    event = str(row.get('event') or '').strip()
    if str(row.get('state') or '') == 'batch_step' or event == phase:
        event = ''  # 阶段名已经把事件说清楚了，不必说两遍
    body = ' · '.join(part for part in (f'第 {pot} 锅' if pot > 0 else '', event) if part)
    line = f'{stamp} [{phase}]' + (' ' + body if body else '')
    detail = str(row.get('detail') or '').strip()
    return line + ('：' + detail if detail else '') + '\n'
