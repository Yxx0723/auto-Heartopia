"""P1 selects a single explicit recipe, verifies readback and stops."""
import math
import traceback
from utils.core.cooking_catalog import material_status
from utils.core.scene_read import SceneReadIssue
from utils.drivers.cooking_menu_state import CookingMenuDetailMismatch, CookingMenuStateChanged


def select_recipe(*, recipe, reader, window, controls, device, inventory, clock, report, timeout=30.):
    identity = recipe['recipe_id']; size = tuple(window.size())
    deadline = clock.monotonic()+timeout
    scope = stove = None
    clicked = False; last_view = None; unchanged = 0
    sync_first_at = None; sync_reads = 0; last_sync = None
    def guard():
        running, stopped = controls.snapshot()
        if stopped:
            raise ValueError('选菜已停止，未开始制作')
        if not running or not window.foreground() or tuple(window.size()) != size:
            controls.pause()
            raise ValueError('选菜暂停：请保持游戏前台及原窗口尺寸，重新确认后再试')
    def wait(seconds):
        end = min(deadline, clock.monotonic()+seconds)
        while clock.monotonic() < end:
            guard()
            remaining = end-clock.monotonic()
            if remaining <= 0:
                break
            clock.sleep(min(.02, remaining))
        guard()
    def selection_with_ui_recovery():
        # A discarded UI snapshot gets at most two fresh read-only replacements.
        expected_scope, expected_stove = scope, stove
        recovering = False
        for ui_attempt in range(1, 4):
            guard()
            if recovering and clock.monotonic() >= deadline:
                raise ValueError('食谱 UI 只读恢复达到原选菜截止时间，未开始制作')
            try:
                value = reader.selection(size)
            except CookingMenuStateChanged as exc:
                report('recipe_snapshot_changed', '已丢弃不一致的食谱读取',
                    attempt=ui_attempt, snapshot_diagnostic=exc.diagnostics)
                if not exc.recoverable or exc.classification != 'UI_STACK_CHANGED':
                    raise
                evidence = exc.diagnostics
                observed_scope, observed_stove = evidence.get('scope'), evidence.get('stove_identity')
                if not observed_scope or not observed_stove:
                    raise
                if expected_scope is None:
                    expected_scope, expected_stove = observed_scope, observed_stove
                if observed_scope != expected_scope or observed_stove != expected_stove:
                    raise ValueError('食谱 UI 恢复时灶台或场景身份改变，未继续选菜') from exc
                recovering = True
                if ui_attempt == 3:
                    failed = {**evidence, 'recoverable': False,
                        'stop_reason': 'UI 只读读取已达 3 次上限'}
                    report('recipe_snapshot_retry_exhausted', failed['stop_reason'],
                        attempt=ui_attempt, snapshot_diagnostic=failed)
                    raise CookingMenuStateChanged(failed) from exc
                if clock.monotonic() >= deadline:
                    raise ValueError('食谱 UI 只读恢复达到原选菜截止时间，未开始制作') from exc
                continue
            except Exception as exc:
                if recovering:
                    # Do not let the older invalid-reference/detail retry paths
                    # extend or restart this three-read recovery budget.
                    failed = {'classification': 'READ_FAILED', 'recoverable': False,
                        'stop_reason': 'UI 恢复重新读取失败',
                        'read_error': {'type': type(exc).__name__, 'message': str(exc)}}
                    report('recipe_snapshot_read_failed', failed['stop_reason'],
                        attempt=ui_attempt, snapshot_diagnostic=failed)
                    raise CookingMenuStateChanged(failed) from exc
                raise
            if recovering:
                guard()
                if clock.monotonic() >= deadline:
                    raise ValueError('食谱 UI 只读恢复达到原选菜截止时间，未开始制作')
                if value['scope'] != expected_scope or value['stove_identity'] != expected_stove:
                    raise ValueError('食谱 UI 恢复时灶台或场景身份改变，未继续选菜')
                report('recipe_snapshot_recovered', '食谱 UI 只读读取已恢复，继续完整料理核对',
                    attempts=ui_attempt)
            return value

    def observe():
        nonlocal scope, stove, sync_first_at, sync_reads, last_sync
        # A just-opened recipe list can replace its card references while it
        # initializes. Discard the whole failed read; never act on part of it.
        read_deadline = min(deadline, clock.monotonic()+2.)
        for attempt in range(1, 6):
            guard()
            try:
                value = selection_with_ui_recovery()
                break
            except CookingMenuDetailMismatch as exc:
                # A click has already been dispatched. Read-only reconciliation
                # is allowed only for this one specific panel/system mismatch.
                if not clicked:
                    raise
                device.release_all()
                pair = (exc.panel_detail, exc.system_detail)
                if sync_first_at is None:
                    sync_first_at = clock.monotonic()
                sync_reads += 1
                if pair != last_sync:
                    report('recipe_detail_sync_pending',
                           '选菜后面板与系统详情尚未一致，仅等待读回，不重复点击',
                           recipe_id=identity, panel_detail=pair[0],
                           system_detail=pair[1])
                last_sync = pair
                if clock.monotonic() >= deadline:
                    raise ValueError('选菜后食谱详情未在原确认期限内同步，未开始制作') from exc
                wait(min(.1, deadline-clock.monotonic()))
                return None
            except SceneReadIssue as exc:
                if exc.kind != 'invalid_reference':
                    raise
                device.release_all()
                frames = traceback.extract_tb(exc.__traceback__)
                location = [{'function': f.name, 'line': f.lineno, 'source': f.line}
                            for f in frames[-4:]]
                report('recipe_read_retry', '料理控件引用未就绪，正在重新读取',
                       attempt=attempt, last_read_error={'type': type(exc).__name__,
                       'message': str(exc), 'kind': exc.kind, 'stage': exc.stage,
                       'location': location})
                if attempt == 5 or clock.monotonic()+.2 >= read_deadline:
                    raise ValueError('料理控件引用连续读取失败，已停止；最后异常：'
                                     +type(exc).__name__+': '+str(exc)) from exc
                wait(.2)
        if attempt > 1:
            report('recipe_read_recovered', '料理控件读取已恢复，继续核对所选料理', attempts=attempt)
        if last_sync is not None:
            report('recipe_detail_sync_recovered',
                   '面板与系统详情已恢复一致，继续核对目标料理 ID',
                   recipe_id=identity, observations=sync_reads,
                   elapsed_ms=round((clock.monotonic()-sync_first_at)*1000, 3))
            last_sync = None; sync_first_at = None; sync_reads = 0
        if scope is None:
            scope, stove = value['scope'], value['stove_identity']
        if value['scope'] != scope or value['stove_identity'] != stove:
            raise ValueError('灶台或场景已经改变，未继续选菜')
        return value
    try:
        if recipe['status'] != 'confirmed':
            raise ValueError('料理库编号尚未确认，不能自动选菜')
        if not material_status(recipe, inventory())['sufficient']:
            raise ValueError('当前随身背包可用材料不足；未更换所选料理')
        while clock.monotonic() < deadline:
            value = observe()
            if value is None:
                continue  # The only recoverable mismatch is after a card click.
            if identity not in value['available_ids']:
                raise ValueError('当前灶台没有所选食谱：可能尚未解锁或灶台类别不符；未替换料理')
            selected = value.get('selected') or {}
            if selected.get('recipe_id') == identity and selected.get('name') == recipe['name']:
                wait(.12); second = observe()
                if second is None:
                    continue
                if second['signature'] == value['signature'] and second.get('selected') == selected:
                    stock = inventory(); materials = material_status(recipe, stock)
                    if not materials['sufficient']:
                        raise ValueError('选菜后背包材料变化或不足；已停止，未开始制作')
                    result = {'recipe_id': identity, 'recipe_name': recipe['name'], 'started_pots': 0,
                              'inventory': stock, 'materials': materials}
                    report('RECIPE_SELECTED', '已确认游戏内料理：'+recipe['name']+'；P1 到此停止，未开始制作', **result)
                    return result
                continue
            if clicked:
                wait(.1)
                continue  # A dispatched card click is confirmed, never blindly repeated.
            cards = [c for c in value['cards'] if c['recipe_id'] == identity]
            if cards:
                card = cards[0]; wait(.12); fresh = observe()
                if card not in fresh['cards']:
                    continue
                l,t,r,b = card['rect']; point = (round((l+r)/2), round((t+b)/2))
                guard(); device.execute({'type': 'cursor_move', 'x': point[0], 'y': point[1]})
                wait(.03); pointer = window.pointer()
                if len(pointer) != 3 or not pointer[2] or math.dist(pointer[:2], point) > 2:
                    raise ValueError('鼠标未到达当前料理控件，未点击')
                # One final owned-layout read after positioning, before mouse down.
                if card not in observe()['cards']:
                    continue
                clicked = True
                try:
                    guard(); device.execute({'type': 'mouse_down', 'button': 'left'})
                    wait(.04)
                finally:
                    device.release_all()
                report('recipe_confirming', '已点击指定料理，等待读回确认；不会点击开始制作')
                wait(.15)
                continue
            visible = tuple(c['recipe_id'] for c in value['cards'])
            unchanged = unchanged+1 if visible == last_view else 0; last_view = visible
            if unchanged >= 3:
                raise ValueError('料理列表滚动没有进展，未点击其他料理')
            order = value['available_ids']; target_index = order.index(identity)
            indices = [order.index(i) for i in visible if i in order]
            direction = 1 if indices and target_index < min(indices) else -1
            l,t,r,b = value['viewport']; point = (round((l+r)/2), round((t+b)/2))
            if not 0 < l < r < size[0] or not 0 < t < b < size[1]:
                raise ValueError('当前料理滚动区域不在游戏客户区内')
            guard(); device.execute({'type':'cursor_move','x':point[0],'y':point[1]})
            wait(.03); pointer = window.pointer()
            if not pointer[2] or math.dist(pointer[:2],point)>2:
                raise ValueError('鼠标未到达食谱列表，未滚动')
            guard(); device.execute({'type':'scroll','dx':0,'dy':direction*2})
            wait(.3)
        raise ValueError('选菜后详情未同步，原确认期限已到，未开始制作' if last_sync is not None
                         else '指定料理选中确认超时，未开始制作')
    finally:
        device.release_all()
