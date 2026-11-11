"""Read the first-cook bottom dialog without issuing game input."""
from utils.core.ui_projection import rect_to_client


def _dialog_snapshot(ui):
    meta = ui.meta
    scene = ui.scene_context()
    stack = ui.ui_views()
    panels = []
    for view in stack[3]:
        info = meta.obj(view)
        if (info['name'], info['namespace']) == ('BottomDialogPanel', 'XDTGame.UI.Panel'):
            panels.append(view)
    if not panels:
        return scene, stack, None
    if len(panels) != 1:
        raise ValueError('底部对话候选不唯一')
    panel = panels[0]
    active = ui.payload(panel, '_viewActiveCache', 2, 1)
    if active == b'\x00':
        return scene, stack, (panel, None, None, None, None)
    if active != b'\x01' or ui.payload(panel, '_isGotFocus', 2, 1) != b'\x01':
        raise ValueError('底部对话激活或焦点未确认')
    nodes = meta.ref(panel, 'ui')
    ui.expected(nodes, 'BottomDialogPanel_Auto', 'XDTGame.Auto')
    data = meta.ref(panel, '<ViewData>k__BackingField')
    ui.expected(data, 'BottomDialogPanelViewData', 'XDTGame.UI.Panel')
    pointer = int.from_bytes(ui.payload(data, 'message', 14, 8), 'little')
    return scene, stack, (panel, nodes, data, pointer, meta.managed_string(pointer))


def _button_evidence(reader, window, stack, nodes, data):
    ui, layout = reader.ui, reader.layout
    meta = ui.meta

    def bindings():
        button = meta.ref(nodes, 'cell_btn')
        ui.expected(button, 'CustomButton', 'XDTGUI.View.Components')
        cell = meta.ref(nodes, 'cell_go')
        ui.expected(cell, 'GameObject', 'UnityEngine')
        if not layout.active(cell):
            raise ValueError('底部对话按钮对象未显示')
        handler = meta.ref(button, '_clickHandler')
        delegate = meta.ref(data, 'onCellClick')
        ui.expected(handler, 'Action', 'System')
        ui.expected(delegate, 'Action', 'System')
        target = meta.ref(handler, '_target')
        logic = meta.ref(delegate, '_target')
        ui.expected(target, 'BottomDialogPanel', 'XDTGame.UI.Panel')
        if meta.ref(target, 'ui') != nodes:
            raise ValueError('底部对话按钮回调归属已变化')
        ui.expected(logic, 'BottomDialogPanelLogic', 'XDTGame.UI.Logic')
        callback = meta.ref(logic, '_clickCallback')
        ui.expected(callback, 'Action', 'System')
        callback_target = meta.ref(callback, '_target')
        if callback_target:
            meta.obj(callback_target)
        canvas = meta.ref(stack[1], '<CanvasRect>k__BackingField')
        return button, cell, handler, target, delegate, logic, callback, callback_target, canvas

    first = bindings()
    button, cell, handler, target, delegate, logic, callback, callback_target, canvas = first
    left, top, right, bottom = window.get_rect()
    size = right - left, bottom - top
    rect, chain = layout.button_geometry(button)
    client = rect_to_client(rect, chain, layout.rect(canvas)['rect'], layout.transforms(canvas), size)
    x1, y1, x2, y2 = client
    if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
        raise ValueError('底部对话按钮投影超出游戏客户区')
    if first != bindings() or (left, top, right, bottom) != window.get_rect():
        raise ValueError('底部对话按钮或窗口在读取期间变化')
    return {
        'state': 'geometry_observed', 'button': button, 'cell': cell, 'handler': handler,
        'handler_target': target, 'delegate': delegate, 'logic': logic, 'callback': callback,
        'callback_target': callback_target, 'client_rect': client,
        'screen_rect': (left + x1, top + y1, left + x2, top + y2),
        'hit_verified': False,
    }


def observe_first_recipe_notice(reader, window, recipe):
    """Classify the observed message and record optional button evidence."""
    result = {'state': 'uncertain', 'recipe_id': recipe['recipe_id'], 'auto_click_enabled': False}
    try:
        first = _dialog_snapshot(reader.ui)
        _, stack, dialog = first
        if first != _dialog_snapshot(reader.ui):
            raise ValueError('底部对话或场景引用在读取期间变化')
        if dialog is None or dialog[1] is None:
            return {**result, 'state': 'absent'}
        panel, nodes, data, _, message = dialog
        result.update(panel=panel, view_data=data, message=message)
        if recipe['recipe_id'] != 45164 or message != '好棒！第一次成功做出了菠萝酱！':
            return {**result, 'reason': '底部对话未匹配已验证的料理和完整消息'}
        try:
            result['button'] = _button_evidence(reader, window, stack, nodes, data)
        except Exception as exc:
            result['button'] = {'state': 'unavailable', 'error': type(exc).__name__, 'reason': str(exc)}
        if first != _dialog_snapshot(reader.ui):
            raise ValueError('按钮取证后底部对话或场景引用已变化')
        return {**result, 'state': 'confirmed'}
    except Exception as exc:
        return {**result, 'error': type(exc).__name__, 'reason': str(exc)}
