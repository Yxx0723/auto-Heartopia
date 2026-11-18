"""日志文案：内部代号翻成中文，且不吞掉原始信息。"""
from utils.ui.cooking_event_view import event_view
from utils.ui.cooking_log_text import (batch_status_text, detail_text, human_reason,
                                       log_line, phase_text, stage_label, state_text,
                                       step_summary, step_text)


def test_known_codes_become_chinese_and_unknown_codes_stay_raw():
    assert step_text('p2_single_pot') == '自动开锅'
    assert state_text('batch_stopped') == '已停止'
    assert step_text('brand_new_step') == 'brand_new_step'
    assert state_text('brand_new_state') == 'brand_new_state'
    assert step_text(None) == '' and state_text('') == ''


def test_batch_status_prefers_the_batch_own_state():
    assert batch_status_text({'state': 'batch_finished'}, 'batch_finished') == '已完成'
    assert batch_status_text({'state': 'batch_stopped'}, 'batch_stopped') == '已停止（失败）'
    assert batch_status_text({'state': 'batch_wait'}, 'batch_wait') == '等待开始'
    assert batch_status_text({'state': 'preparing'}, 'batch_preparing') == '进行中'
    assert batch_status_text({}, 'idle') == '空闲'


def test_phase_falls_back_to_state_only_when_it_adds_information():
    batch = {'state': 'batch_preparing', 'diagnostic_context': {'current_step': 'read_material_inventory'}}
    assert step_summary(batch, 'batch_preparing') == '读取背包材料'
    # 没有具体步骤时不要重复一遍批次状态。
    assert step_summary({'state': 'batch_wait'}, 'batch_wait') == ''
    assert step_summary({'state': 'preparing'}, 'unconfirmed') == '未确认'


def test_stage_label_hides_itself_when_it_repeats_the_event():
    assert stage_label('read_material_inventory', 'batch_step') == '读取背包材料'
    assert stage_label('batch_wait', 'batch_wait') == ''
    assert stage_label('open_recipe_panel', 'open_recipe_panel') == ''


def test_phase_label_is_short_and_never_drops_an_unknown_step():
    assert phase_text('p2_single_pot', 'batch_step') == '自动开锅'
    assert phase_text('read_material_inventory', 'batch_step') == '读取材料'
    assert phase_text(None, 'batch_stopped') == '停止'
    assert phase_text('brand_new_step', 'batch_step') == 'brand_new_step'


def test_human_reason_drops_stop_prefix_and_exception_class():
    assert human_reason('连续制作停止：ValueError: 材料不足') == '材料不足'
    assert human_reason('单锅停止：OSError: 进程已退出') == '进程已退出'
    assert human_reason('翻锅回调未确认') == '翻锅回调未确认'
    assert human_reason(None) == ''


def test_detail_text_drops_echoes_but_keeps_real_reasons():
    assert detail_text('batch_step', '完成批次步骤：open_recipe_panel') == ''
    assert detail_text('native_observation', '烹饪原始状态观测') == ''
    assert detail_text('cooking', '读取实际烹饪状态：2') == ''
    assert detail_text('batch_stopped', '连续制作停止：ValueError: 材料不足') == '材料不足'


def test_log_line_reads_like_the_collection_log():
    # 时:分:秒 [阶段] 第 N 锅 · 事件：原因 —— 与采集日志同一读法。
    row = {'time': '23:28:34', 'pot': 2, 'state': 'batch_step', 'stage': 'p2_single_pot',
           'phase': '自动开锅', 'stage_label': '自动开锅', 'event': '自动开锅',
           'status': '已确认', 'detail': ''}
    assert log_line(row) == '23:28:34 [自动开锅] 第 2 锅\n'
    row = {'time': '23:28:34', 'pot': 2, 'state': 'stir_left_diagnostic',
           'phase': '自动开锅', 'event': '翻锅 UI 点击', 'status': '未确认',
           'detail': '未取得 UI 点击确认；不得计为翻锅成功'}
    assert log_line(row) == (
        '23:28:34 [自动开锅] 第 2 锅 · 翻锅 UI 点击：未取得 UI 点击确认；不得计为翻锅成功\n')
    # 还未进入第一锅时不写「第 0 锅」。
    row = {'time': '23:28:34', 'pot': 0, 'state': 'playback_wait', 'phase': '等待',
           'event': '等待开始', 'status': '等待', 'detail': '烹饪已就绪'}
    assert log_line(row) == '23:28:34 [等待] 等待开始：烹饪已就绪\n'
    # 没有 phase 字段时，日志行自己补得出短标签。
    assert log_line({'time': '23:28:34', 'state': 'stopped',
                     'event': '已停止'}) == '23:28:34 [停止] 已停止\n'


def test_event_view_translates_native_and_step_codes():
    view = event_view({'at': '2026-11-18T23:28:27.411000+08:00', 'state': 'native_observation',
                       'message': '烹饪原始状态观测', 'native_state': {'status': 2},
                       'batch': {'current_pot': 2,
                                 'diagnostic_context': {'current_step': 'p2_single_pot'}}})
    assert view['event'] == '原生状态 烹饪中'
    assert view['phase'] == '自动开锅'
    assert view['stage_label'] == '自动开锅'
    assert view['detail'] == ''
    assert view['time'] == '23:28:27'


def test_event_view_marks_a_confirmed_start_as_open():
    view = event_view({'at': '2026-11-18T23:28:20.002000+08:00', 'state': 'start_confirmation',
                       'message': '开锅确认 OPEN_SUCCESS', 'batch': {'current_pot': 1}})
    assert view['event'] == '开锅确认'
    assert view['detail'] == '已开锅'
    assert view['status'] == '已确认'


def test_event_view_uses_the_batch_own_label_for_summary_lines():
    view = event_view({'at': '2026-11-18T23:28:34.000000+08:00', 'state': 'batch_stopped',
                       'message': '连续制作停止：误差', 'batch': {
                           'current_pot': 2,
                           'diagnostic_context': {'current_step': 'p2_single_pot'}}})
    # 停止这条说的是整批，不沿用上一锅的阶段名。
    assert view['phase'] == '停止'
    assert view['status'] == '失败'


def test_event_view_marks_step_completion():
    view = event_view({'at': '2026-11-18T23:27:49.000000+08:00', 'state': 'batch_step',
                       'step_phase': 'complete', 'message': '完成批次步骤：read_material_inventory',
                       'diagnostic_context': {'current_step': 'read_material_inventory'},
                       'batch': {'current_pot': 1}})
    assert view['detail'] == '已完成'
    assert view['phase'] == '读取材料'
    view = event_view({'at': '2026-11-18T23:27:49.000000+08:00', 'state': 'batch_step',
                       'step_phase': 'begin', 'message': '执行批次步骤：read_material_inventory',
                       'diagnostic_context': {'current_step': 'read_material_inventory'},
                       'batch': {'current_pot': 1}})
    assert view['detail'] == ''
