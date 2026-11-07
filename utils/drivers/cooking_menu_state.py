"""Read the observed CookPanel, not the CookingSystem's cached recipe alone."""
import hashlib
import struct

from utils.drivers.cooking_recipe import CookingRecipeReader
from utils.drivers.map_panel import MapPanelReader
from utils.drivers.native_ui_layout import NativeUiLayout


class CookingMenuStateReader:
    def __init__(self,memory):
        self.memory=memory
        self.recipe=CookingRecipeReader(memory)
        self.ui=MapPanelReader(memory)
        self.layout=NativeUiLayout(memory,self.ui.meta)

    def snapshot(self):
        self.memory.locator.snapshot()
        context=self.recipe.context()
        scope=hashlib.sha256(repr((self.recipe.meta.domain,context)).encode()).hexdigest()
        stack=self.ui.ui_views()
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
                raise ValueError('面板实际食谱与当前系统详情未对应')
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
        if self.ui.ui_views()!=stack or self.recipe.context()!=context:
            raise ValueError('食谱界面栈或场景正在变化')
        self.memory.locator.snapshot()
        return result
