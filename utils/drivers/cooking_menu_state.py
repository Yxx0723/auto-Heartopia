"""Read the observed CookPanel, not the CookingSystem's cached recipe alone."""
import hashlib
import struct

from utils.drivers.cooking_recipe import CookingRecipeReader
from utils.drivers.map_panel import MapPanelReader
from utils.drivers.native_ui_layout import NativeUiLayout


class CookingMenuDetailMismatch(ValueError):
    """Panel and CookingSystem recipe details have not been verified equal."""
    def __init__(self, panel_detail, system_detail):
        super().__init__('面板实际食谱与当前系统详情未对应')
        self.panel_detail = panel_detail
        self.system_detail = system_detail


class CookingMenuStateChanged(ValueError):
    """Discarded snapshot with separate UI/context evidence, never a permit."""
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        self.classification = diagnostics['classification']
        self.recoverable = diagnostics.get('recoverable', False)
        super().__init__('食谱读取不一致：'+self.classification+'；未使用该快照'
                         + ('；'+diagnostics['stop_reason'] if diagnostics.get('stop_reason') else ''))


def _observe_menu_sources(ui, recipe):
    # Both reads must run, even when the first fails. No game input is sent.
    values = {}; errors = {}; exceptions = []
    for name, read in (('ui', ui.ui_views), ('context', recipe.context)):
        try:
            values[name] = read()
            errors[name] = None
        except Exception as exc:
            values[name] = None
            errors[name] = {'type': type(exc).__name__, 'message': str(exc)}
            exceptions.append(exc)
    return values, errors, exceptions


def _menu_changes(before, after):
    names = {'ui': ('service_dictionary', 'ui_manager', 'view_layer_dictionary', 'views'),
             'context': ('current_level_node', 'level_instance', 'cooking_system')}
    changed = {}
    for name, fields in names.items():
        a, b = before.get(name), after.get(name)
        if a is None or b is None:
            changed[name] = None
        elif len(a) != len(fields) or len(b) != len(fields):
            changed[name] = {'structure': {'before': a, 'after': b}}
        else:
            changed[name] = {field: {'before': x, 'after': y}
                             for field, x, y in zip(fields, a, b) if x != y}
    return changed


class CookingMenuStateReader:
    def __init__(self,memory):
        self.memory=memory
        self.recipe=CookingRecipeReader(memory)
        self.ui=MapPanelReader(memory)
        self.layout=NativeUiLayout(memory,self.ui.meta)

    def snapshot(self):
        self.memory.locator.snapshot()
        before, before_errors, errors = _observe_menu_sources(self.ui, self.recipe)
        if errors:
            raise CookingMenuStateChanged({'classification': 'READ_FAILED',
                'before': before, 'after': {'ui': None, 'context': None},
                'before_errors': before_errors, 'after_errors': {},
                'ui_changed': None, 'context_changed': None, 'changed_fields': {},
                'recoverable': False}) from errors[0]
        context=before['context'];stack=before['ui']
        scope=hashlib.sha256(repr((self.recipe.meta.domain,context)).encode()).hexdigest()
        m=self.ui.meta
        panels=[v for v in stack[3] if (m.obj(v)['name'],m.obj(v)['namespace'])
                ==('CookPanel','XDTGame.UI.Panel')]
        if len(panels)>1:
            raise ValueError('食谱面板不唯一，打开状态未确认')
        result={'state':'ready','open':False,'scope':scope,'read_only':True}
        if panels:
            panel=panels[0]
            active=self.ui.payload(panel,'_viewActiveCache',2,1)
            focused=self.ui.payload(panel,'_isGotFocus',2,1)
            if active!=b'\x01' or focused!=b'\x01':
                raise ValueError('食谱面板尚未稳定显示或焦点未确认')
            nodes=m.ref(panel,'ui')
            self.ui.expected(nodes,'CookPanel_Auto','XDTGame.Auto')
            # UIViewBase.prefab is the template, not the instantiated UI root.
            root_names=('background_go','list_go','btnBar_go')
            roots=tuple(m.ref(nodes,name) for name in root_names)
            for root in roots:
                self.ui.expected(root,'GameObject','UnityEngine')
                if not self.layout.active(root):
                    raise ValueError('食谱面板实际显示区域未确认')
            detail=m.ref(panel,'_selectedRecipeDetail')
            system_detail=self.recipe.meta.ref(context[-1],'_recipeDetail')
            if not detail or detail!=system_detail:
                raise CookingMenuDetailMismatch(detail, system_detail)
            identity=tuple(self.ui.payload(panel,name,code,width) for name,code,width in (
                ('_cookerStaticId',8,4),('_cookerNetId',9,4),('_cookerObjectId',11,8)))
            result.update(open=True,stove_identity=[struct.unpack('<i',identity[0])[0],
                struct.unpack('<I',identity[1])[0],struct.unpack('<Q',identity[2])[0]])
            if m.ref(panel,'ui')!=nodes \
                    or m.ref(panel,'_selectedRecipeDetail')!=detail \
                    or self.recipe.meta.ref(context[-1],'_recipeDetail')!=system_detail \
                    or self.ui.payload(panel,'_viewActiveCache',2,1)!=active \
                    or self.ui.payload(panel,'_isGotFocus',2,1)!=focused \
                    or tuple(m.ref(nodes,name) for name in root_names)!=roots \
                    or not all(self.layout.active(root) for root in roots) \
                    or identity!=tuple(self.ui.payload(panel,name,code,width) for name,code,width in (
                        ('_cookerStaticId',8,4),('_cookerNetId',9,4),('_cookerObjectId',11,8))):
                raise ValueError('食谱面板或灶台绑定在读取期间变化')
        after, after_errors, errors = _observe_menu_sources(self.ui, self.recipe)
        changed = _menu_changes(before, after)
        ui_changed = None if after['ui'] is None else after['ui'] != stack
        context_changed = None if after['context'] is None else after['context'] != context
        if errors or ui_changed or context_changed:
            kind = ('READ_FAILED' if errors else 'BOTH_CHANGED' if ui_changed and context_changed
                    else 'COOKING_CONTEXT_CHANGED' if context_changed else 'UI_STACK_CHANGED')
            diagnostic = {'classification': kind, 'before': before, 'after': after,
                'before_errors': before_errors, 'after_errors': after_errors,
                'ui_changed': ui_changed, 'context_changed': context_changed,
                'changed_fields': changed, 'scope': scope,
                'stove_identity': result.get('stove_identity'), 'recoverable': False}
            # Only a views-sequence update may be transient. Manager replacement,
            # disappearing/replaced CookPanel or unverified stove ownership stops.
            if kind == 'UI_STACK_CHANGED' and result['open'] and stack[:3] == after['ui'][:3]:
                try:
                    current_panels = [v for v in after['ui'][3]
                        if (m.obj(v)['name'], m.obj(v)['namespace']) == ('CookPanel','XDTGame.UI.Panel')]
                    final_identity = tuple(self.ui.payload(panel,name,code,width)
                        for name,code,width in (('_cookerStaticId',8,4),
                            ('_cookerNetId',9,4),('_cookerObjectId',11,8)))
                    diagnostic['stove_identity_after'] = [struct.unpack('<i',final_identity[0])[0],
                        struct.unpack('<I',final_identity[1])[0],struct.unpack('<Q',final_identity[2])[0]]
                    if final_identity != identity:
                        diagnostic['changed_fields']['stove_identity'] = {
                            'before': result['stove_identity'],
                            'after': diagnostic['stove_identity_after']}
                    diagnostic['recoverable'] = current_panels == [panel] and final_identity == identity
                except Exception as exc:
                    diagnostic['identity_read_error'] = {'type': type(exc).__name__, 'message': str(exc)}
                    diagnostic['classification'] = 'READ_FAILED'
                    errors.append(exc)
            if errors:
                raise CookingMenuStateChanged(diagnostic) from errors[0]
            raise CookingMenuStateChanged(diagnostic)
        self.memory.locator.snapshot()
        return result
