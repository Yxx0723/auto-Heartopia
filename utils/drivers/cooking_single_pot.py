"""Owned cooking component and native UI observations. Never writes memory."""
import struct
import hashlib

from utils.core.cooking_single_pot import STATUSES
from utils.core.ui_projection import rect_to_client
from utils.drivers.cooking_selection import CookingSelectionReader
from utils.drivers.mono_scene import MemorySceneMap


class CookingObservationUpdating(ValueError):
    """Incomplete UI/state publication; never a click permit."""


class SinglePotReader(CookingSelectionReader):
    def __init__(self, memory):
        super().__init__(memory)
        self.bound = None

    def integer(self, obj, name, code=8, fmt='i', width=4):
        return struct.unpack('<'+fmt, self.ui.payload(obj, name, code, width))[0]

    def entity_net(self, entity):
        m = self.ui.meta; info = m.obj(entity); f = m.field(info['address'], '_netId')
        typ = m.cls(f['type'])
        if f['code'] != 17 or typ['name'] != 'NetId' or typ['size'] != 20:
            raise ValueError('烹饪实体网络身份布局未确认')
        return struct.unpack('<I', m.data(entity+f['offset'], 4))[0]

    def menu_owner(self):
        state = self.snapshot()
        if not state['open']:
            raise ValueError('食谱面板没有打开')
        if self.bound and tuple(state['stove_identity'])!=self.bound[1]:
            raise ValueError('食谱面板已切换到其他灶台，未继续本锅')
        m = self.ui.meta; stack = self.ui.ui_views()
        panels = [p for p in stack[3] if (m.obj(p)['name'], m.obj(p)['namespace']) == ('CookPanel','XDTGame.UI.Panel')]
        if len(panels) != 1:
            raise ValueError('灶台食谱面板不唯一')
        return state, stack, panels[0], m.ref(panels[0], 'ui')

    def materials(self):
        state, stack, panel, nodes = self.menu_owner()
        m = self.ui.meta; view = self.recipe.snapshot()
        detail = m.ref(panel, '_selectedRecipeDetail'); array = m.ref(detail, 'materialSlots')
        refs = m.array_refs(array); slots = []
        for obj in refs:
            self.recipe.expected(obj, 'MaterialSlot')
            slot_type = self.integer(obj, 'slotType', 17)
            if slot_type not in (0, 1):
                raise ValueError('未知材料槽类型')
            filled = self.integer(obj, 'filled', 2, 'B', 1)
            if filled not in (0, 1):
                raise ValueError('材料槽填入状态无效')
            slots.append({'kind': 'group' if slot_type == 0 else 'item',
                'required_id': self.integer(obj, 'materialType', 17) if slot_type == 0 else self.integer(obj, 'materialId'),
                'filled': bool(filled), 'net_id': self.integer(obj, 'filledMaterialNetId', 9, 'I'),
                'item_id': self.integer(obj, 'filledMaterialStaticId')})
        if m.array_refs(array) != refs or self.recipe.snapshot() != view or self.snapshot() != state \
                or self.ui.ui_views() != stack:
            raise CookingObservationUpdating('材料槽正在更新')
        return {**view, 'slots': slots, 'stove_identity': state['stove_identity'],
                'picker_open': self.integer(panel, '_isOpenBag', 2, 'B', 1) == 1,
                'selected_slot': self.integer(panel, '_selectedSlotIndex')}

    def bind(self, stove):
        m = self.ui.meta; context = self.recipe.context()
        sample = self.memory.snapshot(); local = MemorySceneMap.local_hierarchy(sample)
        registry = m.static_ref(m.obj(local)['address'], 'ActiveHierarchies')
        local_entities = {t['pointer'] for t in sample['paths'][0]['trace'] if t['field'] == '<entity>k__BackingField'}
        if len(local_entities) != 1:
            raise ValueError('当前人物网络身份不唯一')
        role = local_entities.pop()
        if self.integer(role, '<isSelfPlayer>k__BackingField', 2, 'B', 1) != 1:
            raise ValueError('当前人物归属未确认')
        matches = []
        for hierarchy in m.dictionary(registry):
            try:
                owner = m.ref(hierarchy, '_hierarchyObject')
                if not owner or m.obj(owner)['name'] not in ('StaticLevelObjectWrap', 'LevelEntityComponent'):
                    continue
                entity = m.ref(owner, '<entity>k__BackingField')
                for component in m.dictionary(m.ref(entity, '_components'), max_items=128):
                    info = m.obj(component)
                    if (info['name'], info['namespace']) != ('CookingComponent', 'XDTLevelAndEntity.Gameplay.Component.Homeland'):
                        continue
                    f = m.field(info['address'], '_componentData'); typ = m.cls(f['type'])
                    if f['code'] != 17 or (typ['name'],typ['namespace']) != ('CookingComponentData','XDTDataAndProtocol.ComponentsData'):
                        continue
                    level_id = struct.unpack('<Q',m.data(component+f['offset']+m.field(typ['address'],'levelObjectNetId')['offset']-16,8))[0]
                    if level_id != stove[2]:
                        continue
                    parent = m.ref(component, '_parentCookerComponent')
                    parent_entity = m.ref(parent, '<entity>k__BackingField')
                    if self.entity_net(entity) != stove[1] and self.entity_net(parent_entity) != stove[1]:
                        continue
                    if self.integer(component, '_cookerStaticId') != stove[0]:
                        continue
                    matches.append((component, entity, parent, parent_entity))
            except (ValueError, OSError):
                continue  # Unrelated streaming objects never authorize a pot.
        if len(matches) != 1 or self.recipe.context() != context:
            raise ValueError('当前灶台的烹饪组件尚未唯一对应')
        self.bound = (context, tuple(stove), role, self.entity_net(role), *matches[0])
        return self.state()

    def state(self):
        if self.bound is None:
            raise ValueError('尚未绑定当前单锅灶台')
        context, stove, role, local_net, component, entity, parent, parent_entity = self.bound
        m = self.ui.meta
        if self.recipe.context() != context or self.entity_net(role) != local_net \
                or self.integer(role,'<isSelfPlayer>k__BackingField',2,'B',1)!=1 \
                or m.ref(component, '<entity>k__BackingField') != entity \
                or m.ref(component, '_parentCookerComponent') != parent \
                or m.ref(parent, '<entity>k__BackingField') != parent_entity \
                or stove[1] not in (self.entity_net(entity),self.entity_net(parent_entity)) \
                or self.integer(component,'_cookerStaticId')!=stove[0]:
            raise ValueError('单锅灶台或人物归属改变')
        info = m.obj(component); f = m.field(info['address'], '_componentData'); typ = m.cls(f['type'])
        if (info['name'],typ['name']) != ('CookingComponent','CookingComponentData'):
            raise ValueError('单锅组件类型改变')
        raw = m.data(component+f['offset'], typ['size']-16)
        def value(name, code, fmt, width):
            ff=m.field(typ['address'], name)
            if ff['code'] != code or ff['flags']&16 or not 16 <= ff['offset'] <= typ['size']-width:
                raise ValueError('原生烹饪状态字段布局不符')
            return struct.unpack_from('<'+fmt,raw,ff['offset']-16)[0]
        # Once a pot starts the recipe panel may be closed/cleared. Poll the
        # bound live component, not the old menu's material references.
        scope=hashlib.sha256(repr((self.recipe.meta.domain,context)).encode()).hexdigest()
        result = {'scope': scope, 'stove_identity': list(stove),
            'burner_net_id': self.entity_net(entity) or self.entity_net(parent_entity), 'local_net_id': local_net,
            'level_object_id': value('levelObjectNetId',11,'Q',8), 'status': value('Status',17,'i',4),
            'status_started': value('CurrentStatusStartTime',17,'Q',8),
            'period_ms': value('PeriodTimeMs',8,'i',4), 'total_ms': value('TotalCookingTimeMs',8,'i',4),
            'food_id': value('FoodItemId',8,'i',4), 'owner_net_id': value('OwnerNetId',9,'I',4),
            'previous_status': self.integer(component,'_oldStatus',17)}
        if result['level_object_id'] != stove[2] or result['status'] not in STATUSES or self.recipe.context() != context:
            raise ValueError('原生烹饪状态或归属不符')
        if m.data(component+f['offset'], len(raw)) != raw:
            raise CookingObservationUpdating('原生烹饪状态正在变化')
        return result

    def _rect(self, component, stack, panel, nodes, size):
        return self.ui._button_rect(self.layout,(*stack[:3],panel,nodes),component,size)

    def _toggle_rect(self, widget, stack, size):
        m=self.ui.meta; n=self.layout.native; toggle=m.ref(widget,'_toggle')
        self.ui.expected(toggle,'Toggle','UnityEngine.UI')
        target=self.layout.resolve(toggle,'Toggle','UnityEngine.UI')
        for name in ('m_Interactable','m_GroupsAllowInteraction'):
            ff=n.field(n.obj(target)['address'],name,2)
            if n.data(target+ff['offset'],1)!=b'\1':
                raise ValueError('材料控件不可交互')
        # This client's PackWidget toggle has a null m_TargetGraphic. Its
        # owned instantiated widget root supplies the actual clickable cell.
        ui=m.ref(widget,'ui');rect=m.ref(ui,'root_t');go=m.ref(ui,'<gameObject>k__BackingField')
        if not self.layout.active(go):
            raise ValueError('材料控件实际实例未显示')
        canvas=m.ref(stack[1],'<CanvasRect>k__BackingField')
        result=rect_to_client(self.layout.rect(rect)['rect'],self.layout.transforms(rect),
            self.layout.rect(canvas)['rect'],self.layout.transforms(canvas),size)
        if m.ref(widget,'_toggle')!=toggle or m.ref(widget,'ui')!=ui or m.ref(ui,'root_t')!=rect \
                or self.layout.resolve(toggle,'Toggle','UnityEngine.UI')!=target:
            raise ValueError('材料控件引用在读取期间变化')
        return result

    def hud_target(self, size, kind, expected):
        current=self.state()
        status=3 if kind=='stir' else 5 if kind=='collect' else -1
        if current['status']!=status or current['status_started']!=expected['status_started']:
            raise ValueError('翻锅或收菜原生事件已经变化')
        m=self.ui.meta;stack=self.ui.ui_views()
        panels=[p for p in stack[3] if (m.obj(p)['name'],m.obj(p)['namespace'])==('TrackingPanel','XDTGame.UI.Panel')]
        if len(panels)!=1 or any(m.obj(p)['name']=='CookPanel' for p in stack[3]):
            raise CookingObservationUpdating('烹饪交互 HUD 尚未显示')
        panel=panels[0];nodes=m.ref(panel,'ui');bar_widget=m.ref(nodes,'tracking_bar_widget')
        track=m.ref(bar_widget,'_trackModule')
        if m.ref(track,'<XDTGame.Core.IModule.Owner>k__BackingField')!=self.bound[0][1]:
            raise ValueError('烹饪 HUD 不属于当前场景')
        bars=m.list_refs(m.ref(track,'_bars'));cells=set()
        wanted='CookDangerTrackCellModel' if kind=='stir' else 'CookResultTrackCellModel'
        for bar in bars:
            if m.obj(bar)['name']!='EntityTrackBarModel':continue
            if self.integer(bar,'<entityId>k__BackingField',9,'I')!=current['burner_net_id']:continue
            if self.integer(bar,'_visible',2,'B',1)!=1:continue
            for cell in m.list_refs(m.ref(bar,'_cells')):
                if (m.obj(cell)['name'],m.obj(cell)['namespace'])== (wanted,'XDTGUI.Module.Track.Cells') \
                        and self.integer(cell,'_valid',2,'B',1)==1:
                    cells.add(cell)
        if len(cells)!=1:raise CookingObservationUpdating('当前灶台的翻锅或收菜事件控件尚未唯一就绪')
        cell=next(iter(cells));ui=m.ref(bar_widget,'ui');matches=[]
        for name,f in m.obj(ui)['fields'].items():
            if not name.startswith('tracking_') or not name.endswith('_list') or f['flags']&16:continue
            group=m.ref(ui,name)
            for icons_bar in m.list_refs(m.ref(group,'_children')):
                if m.obj(icons_bar)['name']!='IconsBarWidget':continue
                if self.integer(icons_bar,'active',2,'B',1)!=1 or self.integer(icons_bar,'_show',2,'B',1)!=1:continue
                bui=m.ref(icons_bar,'ui');children=m.list_refs(m.ref(m.ref(bui,'cells_list'),'_children'))
                for icon in children:
                    ancestor=m.obj(icon);is_icon=False
                    for _ in range(16):
                        if (ancestor['name'],ancestor['namespace'])==('CommonIcon','XDTGame.UI.Widget'):
                            is_icon=True;break
                        if not ancestor['parent']:break
                        ancestor=m.cls(ancestor['parent'])
                    if not is_icon:continue
                    callback=m.ref(icon,'_clickHandler')
                    if not callback or m.ref(callback,'_target')!=cell:continue
                    iui=m.ref(icon,'ui');visible=m.ref(iui,'root_visible_go')
                    if not self.layout.active(visible):continue
                    button=m.ref(iui,'icon_btn')
                    rect=self._rect(button,stack,panel,nodes,size)
                    if m.ref(icon,'_clickHandler')!=callback or m.ref(callback,'_target')!=cell \
                            or m.ref(icon,'ui')!=iui or m.ref(iui,'icon_btn')!=button:
                        raise ValueError('烹饪事件控件正在回收')
                    matches.append(rect)
        after=self.state()
        if len(matches)!=1 or self.ui.ui_views()!=stack or m.list_refs(m.ref(track,'_bars'))!=bars \
                or (after['status'],after['status_started'])!=(status,current['status_started']):
            raise CookingObservationUpdating('当前翻锅或收菜控件未唯一稳定绑定')
        return {'kind':kind,'rect':matches[0],'stove_identity':current['stove_identity'],
                'scope':current['scope'],'event_id':str(current['status_started'])}

    def menu_target(self, size, kind, *, index=None, net_id=None):
        state,stack,panel,nodes=self.menu_owner(); m=self.ui.meta
        result={'kind':kind,'scope':state['scope'],'stove_identity':state['stove_identity']}
        opened=self.integer(panel,'_isOpenBag',2,'B',1)==1
        if kind=='start':
            if opened:raise ValueError('材料选择器尚未关闭，未点击开始')
            result['rect']=self._rect(m.ref(nodes,'confirm_swapbtn'),stack,panel,nodes,size)
        elif kind=='slot':
            widgets=m.list_refs(m.ref(m.ref(nodes,'material_list'),'_children'))
            if type(index) is not int or not 0<=index<len(self.materials()['slots']) or index>=len(widgets):
                raise ValueError('材料槽控件没有对应')
            result.update(index=index,rect=self._toggle_rect(widgets[index],stack,size))
        elif kind in ('material','close_picker'):
            if not opened:raise ValueError('材料选择器尚未打开')
            bag=m.ref(nodes,'bag_widget'); ui=m.ref(bag,'ui')
            if kind=='close_picker':
                result['rect']=self._rect(m.ref(ui,'close_btn'),stack,panel,nodes,size)
            else:
                options=m.ref(bag,'_opt')
                if self.integer(options,'RecipeId')!=self.materials()['selected']['recipe_id'] \
                        or self.integer(options,'SelectedSlotIndex')!=index \
                        or self.integer(panel,'_selectedSlotIndex')!=index:
                    raise ValueError('材料选择器绑定的料理或槽位不符')
                data=m.list_refs(m.ref(bag,'_bagList')); group=m.ref(ui,'pack_list')
                widgets=m.list_refs(m.ref(group,'_children'))
                matches=[]
                for i,row in enumerate(data):
                    ci=self.ui.expected(row,'BackpackItemUiData',''); ff=m.field(ci['address'],'Item'); item=m.cls(ff['type'])
                    net=m.field(item['address'],'netId'); ident=m.field(item['address'],'staticId')
                    raw=m.data(row+ff['offset'],item['size']-16)
                    current=struct.unpack_from('<I',raw,net['offset']-16)[0]
                    if current==net_id:
                        amount=m.field(item['address'],'count')
                        locked=m.field(item['address'],'isLock');disabled=m.field(item['address'],'IsDisable')
                        if struct.unpack_from('<i',raw,amount['offset']-16)[0]<1 \
                                or raw[locked['offset']-16]!=0 or raw[disabled['offset']-16]!=0:
                            raise ValueError('材料选择器中的分堆不足、锁定或禁用，未点击')
                        matches.append((i,struct.unpack_from('<i',raw,ident['offset']-16)[0]))
                if len(matches)!=1:raise ValueError('所需材料分堆未出现在当前选择器')
                i,item_id=matches[0]
                if i>=len(widgets):raise ValueError('材料列表控件尚未生成')
                result.update(index=index,net_id=net_id,item_id=item_id,rect=self._toggle_rect(widgets[i],stack,size))
                viewport=m.ref(group,'_viewPort')
                if viewport:
                    canvas=m.ref(stack[1],'<CanvasRect>k__BackingField')
                    vr=rect_to_client(self.layout.rect(viewport)['rect'],self.layout.transforms(viewport),
                        self.layout.rect(canvas)['rect'],self.layout.transforms(canvas),size)
                else:
                    vr=(0.,0.,float(size[0]),float(size[1]))
                l,t,r,b=result['rect'];vl,vt,vrx,vb=vr
                result.update(viewport=vr,can_scroll=bool(viewport),
                              visible=0<=l<r<=size[0] and 0<=t<b<=size[1] and vl<=l<r<=vrx and vt<=t<b<=vb)
                if m.list_refs(m.ref(bag,'_bagList'))!=data or m.list_refs(m.ref(group,'_children'))!=widgets:
                    raise ValueError('材料列表正在更新')
        else:
            raise ValueError('未知烹饪菜单操作')
        if self.ui.ui_views()!=stack or self.snapshot()!=state:
            raise ValueError('灶台菜单归属正在变化')
        return result
