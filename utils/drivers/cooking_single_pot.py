"""Owned cooking component and native UI observations. Never writes memory."""
import struct
import hashlib
from time import perf_counter

from utils.core.cooking_single_pot import STATUSES
from utils.core.ui_projection import rect_to_client
from utils.drivers.cooking_selection import CookingSelectionReader
from utils.drivers.mono_scene import MemorySceneMap


class CookingObservationUpdating(ValueError):
    """Incomplete UI/state publication; never a click permit."""
    def __init__(self,message,diagnostics=None):
        super().__init__(message)
        self.diagnostics=diagnostics


class SinglePotReader(CookingSelectionReader):
    def __init__(self, memory):
        super().__init__(memory)
        self.bound = None
        self.binding_witness = None
        self.binding_metrics = []

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

    def _binding_source(self):
        m = self.ui.meta; context = self.recipe.context()
        sample = self.memory.snapshot(); local = MemorySceneMap.local_hierarchy(sample)
        registry = m.static_ref(m.obj(local)['address'], 'ActiveHierarchies')
        local_entities = {t['pointer'] for t in sample['paths'][0]['trace'] if t['field'] == '<entity>k__BackingField'}
        if len(local_entities) != 1:
            raise ValueError('当前人物网络身份不唯一')
        role = local_entities.pop()
        if self.integer(role, '<isSelfPlayer>k__BackingField', 2, 'B', 1) != 1:
            raise ValueError('当前人物归属未确认')
        return context, local, registry, role

    def _binding_stamp(self, source, path):
        m = self.ui.meta
        _, local, registry, _ = source
        hierarchy, owner, entity, components = path
        if (m.static_ref(m.obj(local)['address'], 'ActiveHierarchies') != registry
                or m.ref(hierarchy, '_hierarchyObject') != owner
                or m.ref(owner, '<entity>k__BackingField') != entity
                or m.ref(entity, '_components') != components):
            raise ValueError('已绑定灶台的活动层级或组件归属已变化')
        return m.dictionary_stamp(registry), m.dictionary_stamp(components, 128)

    def bind(self, stove):
        # A failed proof never leaves a binding usable by a later state read.
        stove = tuple(stove)
        self.binding_metrics = []
        previous = self.bound
        try:
            if previous is not None:
                state = self._time_binding('quick', lambda: self._revalidate(stove))
                if state is not None:
                    return state
            self.bound = self.binding_witness = None
            return self._time_binding('full', lambda: self._bind_full(stove, previous))
        except Exception:
            self.bound = self.binding_witness = None
            raise

    def _time_binding(self, mode, read):
        started = perf_counter()
        record = {'mode': mode, 'outcome': 'failed'}
        try:
            result = read()
            record['outcome'] = 'verified' if result is not None else 'collection_changed'
            return result
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            record['seconds'] = perf_counter() - started
            self.binding_metrics.append(record)

    def _revalidate(self, stove):
        source, path, stamp = self.binding_witness
        if stove != self.bound[1] or self._binding_source() != source:
            raise ValueError('场景、人物、灶台或活动层级入口已变化')
        before = self._binding_stamp(source, path)
        # The original scan matched the exact types, not just their short names.
        m = self.ui.meta
        info = m.obj(self.bound[4])
        data = m.field(info['address'], '_componentData')
        typ = m.cls(data['type'])
        if ((info['name'], info['namespace']) !=
                ('CookingComponent', 'XDTLevelAndEntity.Gameplay.Component.Homeland')
                or data['code'] != 17 or (typ['name'], typ['namespace']) !=
                ('CookingComponentData', 'XDTDataAndProtocol.ComponentsData')):
            raise ValueError('已绑定灶台组件类型已变化')
        state = self.state()
        after = self._binding_stamp(source, path)
        if before != after:
            raise ValueError('灶台绑定复核期间集合正在变化')
        return state if after == stamp else None

    def _bind_full(self, stove, previous):
        m = self.ui.meta
        source = self._binding_source()
        context, _, registry, role = source
        hierarchies, registry_stamp = m.dictionary(registry, with_stamp=True)
        matches = []
        for hierarchy in hierarchies:
            try:
                owner = m.ref(hierarchy, '_hierarchyObject')
                if not owner or m.obj(owner)['name'] not in ('StaticLevelObjectWrap', 'LevelEntityComponent'):
                    continue
                entity = m.ref(owner, '<entity>k__BackingField')
                components = m.ref(entity, '_components')
                entries, component_stamp = m.dictionary(components, max_items=128, with_stamp=True)
                for component in entries:
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
                    matches.append(((component, entity, parent, parent_entity),
                                    (hierarchy, owner, entity, components), component_stamp))
            except (ValueError, OSError):
                continue  # Unrelated streaming objects never authorize a pot.
        if len(matches) != 1 or self._binding_source() != source:
            raise ValueError('当前灶台的烹饪组件尚未唯一对应')
        binding, path, component_stamp = matches[0]
        stamp = (registry_stamp, component_stamp)
        self.bound = (context, stove, role, self.entity_net(role), *binding)
        if previous is not None and previous != self.bound:
            raise ValueError('重新绑定发现灶台组件或人物身份变化')
        state = self.state()
        if self._binding_stamp(source, path) != stamp:
            raise ValueError('完整绑定期间活动层级或组件集合发生变化')
        self.binding_witness = (source, path, stamp)
        return state

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

    def _rect(self, component, stack, panel, nodes, size, observation=None):
        args=(self.layout,(*stack[:3],panel,nodes),component,size)
        return self.ui._button_rect(*args) if observation is None else self.ui._button_rect(*args,observation)

    def _toggle_rect(self, widget, stack, size, observation=None, *, material_candidate=False):
        m=self.ui.meta; n=self.layout.native; toggle=m.ref(widget,'_toggle')
        self.ui.expected(toggle,'Toggle','UnityEngine.UI')
        target=self.layout.resolve(toggle,'Toggle','UnityEngine.UI')
        for name in ('m_Interactable','m_GroupsAllowInteraction'):
            ff=n.field(n.obj(target)['address'],name,2)
            enabled=n.data(target+ff['offset'],1)==b'\1'
            if material_candidate:
                observation[name]=enabled
            if not enabled:
                raise ValueError('材料控件不可交互')
        # This client's PackWidget toggle has a null m_TargetGraphic. Its
        # owned instantiated widget root supplies the actual clickable cell.
        ui=m.ref(widget,'ui');rect=m.ref(ui,'root_t');go=m.ref(ui,'<gameObject>k__BackingField')
        active=self.layout.active(go)
        if material_candidate:
            observation.update(game_object_active=active, game_object_address=go,
                               toggle_address=toggle, native_toggle_address=target,
                               widget_ui_address=ui, rect_transform=rect)
        if not active:
            raise ValueError('材料控件实际实例未显示')
        canvas=m.ref(stack[1],'<CanvasRect>k__BackingField')
        if observation is not None:
            observation.update(toggle_address=toggle, native_toggle_address=target,
                               widget_ui_address=ui, game_object_address=go)
        local=self.layout.rect(rect)
        if observation is not None:
            observation.update(local_rect=local['rect'], anchored_position=local['anchored_position'],
                               rect_transform=rect, canvas_address=canvas)
        chain=self.layout.transforms(rect)
        canvas_rect=self.layout.rect(canvas)['rect']
        canvas_chain=self.layout.transforms(canvas)
        if observation is not None:
            observation.update(canvas_rect=canvas_rect, canvas_transforms=canvas_chain,
                               slot_transforms=chain,
                               canvas_scale=(size[0]/canvas_rect[2], size[1]/canvas_rect[3]))
        result=rect_to_client(local['rect'],chain,canvas_rect,canvas_chain,size)
        if observation is not None:
            observation['client_rect']=result
        if m.ref(widget,'_toggle')!=toggle or m.ref(widget,'ui')!=ui or m.ref(ui,'root_t')!=rect \
                or self.layout.resolve(toggle,'Toggle','UnityEngine.UI')!=target:
            raise ValueError('材料控件引用在读取期间变化')
        return result

    def _hud_cook_id(self,track,level_object_id):
        """NoticeCook maps levelObjectNetId -> cookNetId before RefreshCook.

        Local burner entities can have _netId==0; the parent's network ID is
        not their HUD entity ID. Read the game's actual mapping, with witnesses.
        """
        m=self.ui.meta;obj=m.ref(track,'_levelObjectNetIdToEntityNetId')
        self.ui.expected(obj,'Dictionary`2','System.Collections.Generic')
        def stamp():
            return (m.ref(track,'_levelObjectNetIdToEntityNetId'),self.integer(obj,'_version'),
                    self.integer(obj,'_count'),self.integer(obj,'_freeCount'),m.ref(obj,'_entries'))
        before=stamp();_,_,count,free,array=before
        if not 0<=free<=count<=4096 or count==free:
            raise CookingObservationUpdating('当前灶台的原生烹饪 HUD 映射尚未就绪')
        ai,capacity=m.array(array,8192);entry=m.cls(ai['element']);stride=entry['size']-16
        key,value,nxt=[m.field(entry['address'],name) for name in ('key','value','next')]
        if count>capacity or not 16<=stride<=64 or (key['code'],value['code'],nxt['code'])!=(11,9,8) \
                or any(f['flags']&16 for f in (key,value,nxt)) \
                or not all(16<=f['offset']<=entry['size']-width for f,width in ((key,8),(value,4),(nxt,4))):
            raise ValueError('烹饪 HUD 映射字典布局不符')
        raw=m.data(array+32,count*stride);matches=[]
        for i in range(count):
            at=i*stride
            if struct.unpack_from('<i',raw,at+nxt['offset']-16)[0]<-1:continue
            if struct.unpack_from('<Q',raw,at+key['offset']-16)[0]==level_object_id:
                matches.append(struct.unpack_from('<I',raw,at+value['offset']-16)[0])
        def stable():
            return stamp()==before and m.data(array+32,len(raw))==raw
        if len(matches)!=1 or matches[0]==0 or not stable():
            raise CookingObservationUpdating('当前灶台的原生烹饪 HUD 身份尚未唯一稳定对应')
        return matches[0],stable

    def _hud_callback_method(self,callback,target,owner,name):
        """Observe the verified Mono delegate binding; never invoke its method."""
        m=self.ui.meta;info=m.obj(callback)
        if (info['name'],info['namespace'])!=('WidgetTrigger`1','XDTGUI.Core.Statics') \
                or m.ref(callback,'_target')!=target or m.ref(callback,'delegates'):
            raise CookingObservationUpdating('翻锅按钮回调不是已确认的单一绑定')
        method=self.integer(callback,'method',24,'Q',8)
        raw=m.data(method,32);klass=struct.unpack_from('<Q',raw,8)[0]
        ci=m.cls(klass);token=struct.unpack_from('<I',raw,4)[0]
        actual=m.text(struct.unpack_from('<Q',raw,24)[0])
        if token>>24!=6 or (ci['name'],ci['namespace'],actual)!=(owner[0],owner[1],name) \
                or self.integer(callback,'method',24,'Q',8)!=method or m.data(method,32)!=raw:
            raise CookingObservationUpdating('翻锅按钮回调方法尚未稳定对应')
        return {'pointer':method,'owner_type':ci['name'],'namespace':ci['namespace'],
                'name':actual,'token':token}

    def hud_target(self, size, kind, expected, *, _revalidate=None):
        current=self.state()
        status=3 if kind=='stir' else 5 if kind=='collect' else -1
        if current['status']!=status or current['status_started']!=expected['status_started']:
            raise ValueError('翻锅或收菜原生事件已经变化')
        if kind=='stir' and _revalidate is None:
            guard_key=(current['scope'],tuple(current['stove_identity']),str(current['status_started']))
            _revalidate=getattr(self,'_danger_revalidated_context',{}).get(guard_key)
        m=self.ui.meta;stack=self.ui.ui_views()
        panels=[p for p in stack[3] if (m.obj(p)['name'],m.obj(p)['namespace'])==('TrackingPanel','XDTGame.UI.Panel')]
        if len(panels)!=1 or any(m.obj(p)['name']=='CookPanel' for p in stack[3]):
            raise CookingObservationUpdating('烹饪交互 HUD 尚未显示')
        panel=panels[0];nodes=m.ref(panel,'ui');bar_widget=m.ref(nodes,'tracking_bar_widget')
        track=m.ref(bar_widget,'_trackModule')
        if m.ref(track,'<XDTGame.Core.IModule.Owner>k__BackingField')!=self.bound[0][1]:
            raise ValueError('烹饪 HUD 不属于当前场景')
        hud_net_id,mapping_stable=self._hud_cook_id(track,current['level_object_id'])
        bars=m.list_refs(m.ref(track,'_bars'));cells=set();owner_bars=set()
        diagnostic={'kind':kind,'stove_identity':current['stove_identity'],
                    'level_object_id':current['level_object_id'],'burner_net_id':current['burner_net_id'],
                    'status':current['status'],'event_id':str(current['status_started']),
                    'hud_cook_net_id':hud_net_id,'bound_burner_entity':self.bound[5],
                    'parent_stove_net_id':current['stove_identity'][1],
                    'burner_raw_net_id':self.entity_net(self.bound[5]),
                    'mapping_source':'TrackModule._levelObjectNetIdToEntityNetId',
                    'bar_count':len(bars),'candidates':[],'widgets':[]}
        if _revalidate is not None and _revalidate.get('record'):
            diagnostic['callback_revalidation']=_revalidate['record']
        diagnostic['callback_revalidation_changes']=(_revalidate.get('changes',[]) if _revalidate is not None else [])
        wanted='CookDangerTrackCellModel' if kind=='stir' else 'CookResultTrackCellModel'
        for bar in bars:
            info=m.obj(bar);bar_type=info['name'];visible=self.integer(bar,'_visible',2,'B',1)==1
            entity_id=(self.integer(bar,'<entityId>k__BackingField',9,'I') if bar_type=='EntityTrackBarModel'
                       else self.integer(bar,'_entityId',9,'I') if bar_type=='ObjectTrackBarModel' else None)
            object_id=self.integer(bar,'_objectId',11,'Q',8) if bar_type=='ObjectTrackBarModel' else None
            entity=m.ref(bar,'_entity') if bar_type=='EntityTrackBarModel' else None
            entity_matches=entity==self.bound[5]
            bar_matches=bar_type=='EntityTrackBarModel' and entity_id==hud_net_id and entity_matches and visible
            for cell in m.list_refs(m.ref(bar,'_cells')):
                ci=m.obj(cell);valid=self.integer(cell,'_valid',2,'B',1)==1
                type_matches=(ci['name'],ci['namespace'])==(wanted,'XDTGUI.Module.Track.Cells')
                phase_matches=True;phase_data={}
                if kind=='stir' and type_matches:
                    end_unix=self.integer(cell,'_endUnix',10,'q',8)
                    duration=self.integer(cell,'_duration',12,'f',4)
                    raw_time=current['status_started'];utc_kind=raw_time>>62
                    expected_end=((raw_time&0x3fffffffffffffff)-621355968000000000)//10000+current['period_ms']
                    phase_matches=utc_kind==1 and end_unix==expected_end and duration==float(current['period_ms'])
                    phase_data={'end_unix_ms':end_unix,'duration_ms':duration,'expected_end_unix_ms':expected_end,
                                'utc_kind':utc_kind,'phase_match':phase_matches}
                if ci['name'].startswith('Cook') or entity_id==hud_net_id or object_id==current['level_object_id']:
                    reasons=[]
                    if bar_type!='EntityTrackBarModel':reasons.append('bar_type')
                    if entity_id!=hud_net_id:reasons.append('hud_entity_id')
                    if not entity_matches:reasons.append('burner_entity')
                    if not visible:reasons.append('bar_not_visible')
                    if not type_matches:reasons.append('cell_type')
                    if not valid:reasons.append('cell_not_valid')
                    if not phase_matches:reasons.append('event_phase')
                    diagnostic['candidates'].append({'bar':bar,'bar_type':bar_type,'bar_namespace':info['namespace'],'cell':cell,
                        'cell_type':ci['name'],'cell_namespace':ci['namespace'],'valid':valid,'visible':visible,
                        'entity_id':entity_id,'object_id':object_id,'entity':entity,'burner_entity_match':entity_matches,
                        'stove_network_match':entity_id==hud_net_id,
                        'resolved_stove_object_id':current['level_object_id'] if entity_id==hud_net_id and entity_matches else object_id,
                        'stove_object_match':object_id==current['level_object_id'],'rejected_by':reasons,**phase_data})
                if bar_matches and type_matches and valid and phase_matches:
                    cells.add(cell);owner_bars.add(bar)
        diagnostic['model_match_count']=len(cells)
        diagnostic['owner_bar_match_count']=len(owner_bars)
        diagnostic['match_stage']='event_model';diagnostic['match_count']=len(cells)
        self.hud_diagnostics=diagnostic
        if len(cells)!=1:
            diagnostic['mapping_stable']=mapping_stable()
            diagnostic['match_result']='none' if not cells else 'multiple'
            raise CookingObservationUpdating(('翻锅' if kind=='stir' else '收菜')+
                ('事件控件无匹配（0 个）' if not cells else '事件控件存在多个匹配（'+str(len(cells))+' 个）'),diagnostic)
        if len(owner_bars)!=1:
            diagnostic.update(match_stage='owner_bar',match_count=len(owner_bars),match_result='multiple')
            raise CookingObservationUpdating('事件模型对应多个灶台提示条，未选择任一控件',diagnostic)
        owner_bar=next(iter(owner_bars))
        cell=next(iter(cells))
        danger_token=self.integer(cell,'<token>k__BackingField') if kind=='stir' else None
        if _revalidate is not None and (current!=_revalidate['native'] or cell!=_revalidate['model'] or danger_token!=_revalidate['token']):
            raise ValueError('回调重新验证时灶台、Danger 或 Token 已改变，未点击')
        ui=m.ref(bar_widget,'ui');matches=[];direct_bindings=[];collect_bindings=[]
        diagnostic['ui_candidates']=[]
        for name,f in m.obj(ui)['fields'].items():
            if not name.startswith('tracking_') or not name.endswith('_list') or f['flags']&16:continue
            group=m.ref(ui,name);group_children=m.list_refs(m.ref(group,'_children'))
            for icons_bar in group_children:
                wi=m.obj(icons_bar)
                diagnostic['ui_candidates'].append({'ui_list':name,'parent_list':group,
                    'widget':icons_bar,'type':wi['name'],'namespace':wi['namespace']})
                # CookDanger is rendered directly by CommonTrackingBar, not
                # inside IconsBarWidget.cells_list. This exact branch was
                # verified against both live Danger phases and native UI IL.
                if kind=='stir' and name=='tracking_cook_danger_list' \
                        and (wi['name'],wi['namespace'])==('CommonIconForCookDanger','XDTGame.UI.Widget'):
                    icon=icons_bar;iui=m.ref(icon,'ui');callback=m.ref(icon,'_clickHandler')
                    target=m.ref(callback,'_target') if callback else None
                    go=m.ref(iui,'<gameObject>k__BackingField');visible=m.ref(iui,'root_visible_go')
                    active=self.layout.active(go);shown=self.layout.active(visible)
                    row={'icon':icon,'icon_type':wi['name'],'ui_list':name,'parent_list':group,
                        'callback_address':callback,'callback_target':target,'callback_type':m.obj(target)['name'] if target else None,
                        'event_model_match':target==cell,'root_active':active,'root_visible':shown}
                    diagnostic['widgets'].append(row)
                    if target!=cell or not active or not shown:continue
                    row['callback_method']=self._hud_callback_method(callback,cell,
                        ('CookDangerTrackCellModel','XDTGUI.Module.Track.Cells'),'TriggerOnClickByView')
                    button=m.ref(iui,'icon_btn');button_callback=m.ref(button,'_clickHandler')
                    self.ui.expected(button,'CustomButton','XDTGUI.View.Components')
                    row['button_callback_method']=self._hud_callback_method(button_callback,icon,
                        ('CommonIcon','XDTGame.UI.Widget'),'OnIconClicked')
                    rect=self._rect(button,stack,panel,nodes,size)
                    row.update(button=button,rect=rect,button_interactable=True)
                    matches.append(rect)
                    direct_bindings.append((name,group,group_children,icon,iui,callback,
                        button,button_callback,go,visible,rect,
                        row['callback_method'],row['button_callback_method']))
                if m.obj(icons_bar)['name']!='IconsBarWidget':continue
                widget_active=self.integer(icons_bar,'active',2,'B',1)==1
                widget_shown=self.integer(icons_bar,'_show',2,'B',1)==1
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
                    target=m.ref(callback,'_target') if callback else None
                    target_type=m.obj(target)['name'] if target else None
                    if target!=cell and target_type not in ('CookDangerTrackCellModel','CookResultTrackCellModel'):continue
                    iui=m.ref(icon,'ui');visible=m.ref(iui,'root_visible_go')
                    root_visible=self.layout.active(visible)
                    # Live Succeed uses CommonIconForInteract in the common
                    # list. IconsBarWidget._show remains false even while its
                    # instantiated result icon is visible; verify the actual
                    # native icon and button instead of that presentation flag.
                    if kind=='collect' and name=='tracking_common_list' \
                            and (m.obj(icon)['name'],m.obj(icon)['namespace'])==('CommonIconForInteract','XDTGame.UI.Widget'):
                        go=m.ref(iui,'<gameObject>k__BackingField')
                        active=self.layout.active(go)
                        row={'icon':icon,'icon_type':m.obj(icon)['name'],'ui_list':name,
                            'callback_target':target,'callback_type':target_type,'event_model_match':target==cell,
                            'bar_active':widget_active,'bar_shown':widget_shown,'root_active':active,
                            'root_visible':root_visible,'level_object_id':current['level_object_id'],
                            'hud_cook_net_id':hud_net_id}
                        diagnostic['widgets'].append(row)
                        if target!=cell or not widget_active or not active or not root_visible:continue
                        method=self._hud_callback_method(callback,cell,
                            ('CookResultTrackCellModel','XDTGUI.Module.Track.Cells'),'TriggerOnClickByView')
                        button=m.ref(iui,'icon_btn');self.ui.expected(button,'CustomButton','XDTGUI.View.Components')
                        button_callback=m.ref(button,'_clickHandler')
                        button_method=self._hud_callback_method(button_callback,icon,
                            ('CommonIcon','XDTGame.UI.Widget'),'OnIconClicked')
                        rect=self._rect(button,stack,panel,nodes,size)
                        row.update(button=button,rect=rect,button_interactable=True,
                            callback_method=method,button_callback_method=button_method)
                        matches.append(rect)
                        collect_bindings.append((group,group_children,icons_bar,bui,children,
                            icon,iui,callback,button,button_callback,go,visible,rect,method,button_method))
                        continue
                    diagnostic['widgets'].append({'icon':icon,'icon_type':m.obj(icon)['name'],'ui_list':name,
                        'callback_target':target,'callback_type':target_type,'event_model_match':target==cell,
                        'bar_active':widget_active,'bar_shown':widget_shown,'root_visible':root_visible,
                        'level_object_id':current['level_object_id'] if target==cell else None,
                        'hud_cook_net_id':hud_net_id if target==cell else None})
                    if target!=cell or not widget_active or not widget_shown or not root_visible:continue
                    button=m.ref(iui,'icon_btn')
                    rect=self._rect(button,stack,panel,nodes,size)
                    if m.ref(icon,'_clickHandler')!=callback or m.ref(callback,'_target')!=cell \
                            or m.ref(icon,'ui')!=iui or m.ref(iui,'icon_btn')!=button:
                        raise ValueError('烹饪事件控件正在回收')
                    matches.append(rect)
        after=self.state()
        diagnostic['widget_match_count']=len(matches)
        diagnostic['match_stage']='widget';diagnostic['match_count']=len(matches)
        diagnostic['match_result']='unique' if len(matches)==1 else 'none' if not matches else 'multiple'
        diagnostic['after_status']=after['status'];diagnostic['after_event_id']=str(after['status_started'])
        diagnostic['mapping_stable']=mapping_stable()
        diagnostic['ui_stack_stable']=self.ui.ui_views()==stack
        diagnostic['bars_stable']=m.list_refs(m.ref(track,'_bars'))==bars
        diagnostic['model_binding_stable']=(m.ref(owner_bar,'_entity')==self.bound[5]
            and self.integer(owner_bar,'<entityId>k__BackingField',9,'I')==hud_net_id
            and self.integer(owner_bar,'_visible',2,'B',1)==1
            and cell in m.list_refs(m.ref(owner_bar,'_cells')) and self.integer(cell,'_valid',2,'B',1)==1)
        diagnostic['phase_stable']=(after['status'],after['status_started'])==(status,current['status_started'])
        diagnostic['binding_stable']=all(diagnostic[k] for k in ('mapping_stable','ui_stack_stable','bars_stable','model_binding_stable','phase_stable'))
        if direct_bindings:
            diagnostic['direct_binding_checks']=[]
            diagnostic['direct_binding_failed_fields']=[]
            diagnostic['direct_binding_before']=[]
            diagnostic['direct_binding_after']=[]
            class CallbackChanged(Exception):pass
            def direct_stable(binding):
                (name,group,children,icon,iui,callback,button,button_callback,
                    go,visible,rect,callback_method,button_method)=binding
                if _revalidate is not None:
                    witness=(name,group,children,icon,iui,button,button_callback,go,visible,rect,callback_method,button_method)
                    if witness!=_revalidate['witness'] or callback!=_revalidate['new_callback']:
                        if callback!=_revalidate['new_callback']:
                            diagnostic['callback_revalidation_changes'].append({
                                'event_id':str(current['status_started']),'icon':icon,'button':button,'model':cell,
                                'old_callback':_revalidate['new_callback'],'new_callback':callback,
                                'stage':'rescan_binding','new_target':cell,'new_method':callback_method,
                                'method_owner':callback,'allow_dispatch':False})
                        raise ValueError('回调重新验证期间控件身份、位置或回调再次变化，未点击')
                checks=[
                    ('bar_widget.ui',ui,lambda:m.ref(bar_widget,'ui')),
                    ('ui.'+name,group,lambda:m.ref(ui,name)),
                    ('group._children',children,lambda:m.list_refs(m.ref(group,'_children'))),
                    ('icon.ui',iui,lambda:m.ref(icon,'ui')),
                    ('icon._clickHandler',callback,lambda:m.ref(icon,'_clickHandler')),
                    ('icon_callback._target',cell,lambda:m.ref(callback,'_target')),
                    ('ui.icon_btn',button,lambda:m.ref(iui,'icon_btn')),
                    ('button._clickHandler',button_callback,lambda:m.ref(button,'_clickHandler')),
                    ('button_callback._target',icon,lambda:m.ref(button_callback,'_target')),
                    ('ui.gameObject',go,lambda:m.ref(iui,'<gameObject>k__BackingField')),
                    ('gameObject.active',True,lambda:self.layout.active(go)),
                    ('ui.root_visible_go',visible,lambda:m.ref(iui,'root_visible_go')),
                    ('root_visible_go.active',True,lambda:self.layout.active(visible)),
                    ('icon_callback.method',callback_method,lambda:self._hud_callback_method(callback,cell,
                        ('CookDangerTrackCellModel','XDTGUI.Module.Track.Cells'),'TriggerOnClickByView')),
                    ('button_callback.method',button_method,lambda:self._hud_callback_method(button_callback,icon,
                        ('CommonIcon','XDTGame.UI.Widget'),'OnIconClicked')),
                    ('button.rect',rect,lambda:self._rect(button,stack,panel,nodes,size)),
                ]
                before={'icon':icon,'ui_list':name,'values':{}}
                after_values={'icon':icon,'ui_list':name,'values':{}}
                diagnostic['direct_binding_before'].append(before)
                diagnostic['direct_binding_after'].append(after_values)
                first_error=None;passed=True
                for field,original,read_value in checks:
                    row={'icon':icon,'ui_list':name,'field':field,'before':original}
                    before['values'][field]=original
                    try:
                        value=read_value()
                        if field=='icon._clickHandler' and value!=original:
                            raise CallbackChanged(binding,value)
                        equal=value==original  # Strict equality, including rectangle and list order.
                        row.update(after=value,equal=equal)
                        after_values['values'][field]=value
                        if field=='button.rect':
                            row['max_coordinate_delta']=max(abs(a-b) for a,b in zip(original,value))
                        if field=='group._children':
                            from collections import Counter
                            old_counts,new_counts=Counter(original),Counter(value)
                            row.update(added=list((new_counts-old_counts).elements()),
                                removed=list((old_counts-new_counts).elements()),
                                order_changed=old_counts==new_counts and value!=original)
                    except CallbackChanged:raise
                    except Exception as exc:
                        if first_error is None:first_error=exc
                        row.update(equal=False,after={'state':'unavailable'},
                            error={'type':type(exc).__name__,'message':str(exc),
                                   'stage':'direct_stable.'+field})
                        after_values['values'][field]={'state':'unavailable','error':row['error']}
                    diagnostic['direct_binding_checks'].append(row)
                    if not row['equal']:
                        passed=False
                        diagnostic['direct_binding_failed_fields'].append({'icon':icon,'ui_list':name,'field':field})
                if first_error is not None:
                    diagnostic['direct_widget_binding_stable']=False
                    diagnostic['binding_stable']=False
                    # Retain the original read exception; diagnostics cannot authorize a click.
                    raise first_error
                return passed
            try:
                diagnostic['direct_widget_binding_stable']=all(direct_stable(b) for b in direct_bindings)
            except CallbackChanged as changed:
                binding,new_callback=changed.args
                (name,group,children,icon,iui,old_callback,button,button_callback,
                    go,visible,rect,method,button_method)=binding
                key=(current['scope'],tuple(current['stove_identity']),str(current['status_started']))
                used=getattr(self,'_danger_revalidation_used',set())
                self._danger_revalidation_used=used
                record={'event_id':str(current['status_started']),'icon':icon,'button':button,'model':cell,
                    'old_callback':old_callback,'new_callback':new_callback,'token':danger_token,
                    'passed':False,'allow_dispatch':False}
                diagnostic['callback_revalidation']=record
                diagnostic['callback_revalidation_changes'].append({
                    'event_id':record['event_id'],'icon':icon,'button':button,'model':cell,
                    'old_callback':old_callback,'new_callback':new_callback,'stage':'direct_stable'})
                if len(matches)!=1 or not diagnostic['binding_stable']:
                    record['stop_reason']='original scene/model/UI checks failed'
                    raise ValueError('回调换址时原有身份安全检查未通过，未点击')
                if _revalidate is not None or key in used:
                    record['stop_reason']='same Danger revalidation budget exhausted'
                    raise ValueError('同一 Danger 回调再次变化，已停止且未点击')
                used.add(key)
                context={'native':current,'model':cell,'token':danger_token,'new_callback':new_callback,
                    'changes':diagnostic['callback_revalidation_changes'],
                    'witness':(name,group,children,icon,iui,button,button_callback,go,visible,rect,method,button_method)}
                try:
                    result=self.hud_target(size,kind,expected,_revalidate=context)
                    fresh=self.hud_diagnostics
                    observed=next((row for row in fresh.get('widgets',[])
                        if row.get('icon')==icon and row.get('callback_address')==new_callback),{})
                    record.update(new_target=observed.get('callback_target',{'state':'unavailable'}),
                        new_method=observed.get('callback_method',{'state':'unavailable'}),
                        new_method_owner=observed.get('callback_address'),
                        evidence_source='complete_hud_rescan.widgets',
                        address_recheck=m.ref(icon,'_clickHandler'))
                    if record['address_recheck']!=new_callback:
                        fresh['callback_revalidation_changes'].append({
                            'event_id':record['event_id'],'icon':icon,'button':button,'model':cell,
                            'old_callback':new_callback,'new_callback':record['address_recheck'],
                            'stage':'final_address_recheck','allow_dispatch':False})
                    if (record['address_recheck']!=new_callback or self.state()!=current
                            or self.integer(cell,'<token>k__BackingField')!=danger_token
                            or self.integer(cell,'_valid',2,'B',1)!=1):
                        raise ValueError('回调重新验证后引用或 Danger 再次变化，未点击')
                    record.update(passed=True,allow_dispatch=True,stop_reason=None)
                    fresh['callback_revalidation']=record
                    contexts=getattr(self,'_danger_revalidated_context',{})
                    context['record']=record
                    contexts[key]=context;self._danger_revalidated_context=contexts
                    return result
                except Exception as exc:
                    rows=self.hud_diagnostics.get('widgets',[])
                    observed=next((row for row in rows if row.get('icon')==icon and row.get('callback_address')==new_callback),{})
                    record.update(new_target=observed.get('callback_target',{'state':'unavailable'}),
                        new_method=observed.get('callback_method',{'state':'unavailable'}),
                        stop_reason=str(exc),error_type=type(exc).__name__)
                    self.hud_diagnostics['callback_revalidation']=record
                    raise ValueError('回调只读重新验证失败，未点击：'+str(exc)) from exc
            diagnostic['binding_stable'] &= diagnostic['direct_widget_binding_stable']
        if collect_bindings:
            def collect_stable(binding):
                (group,group_children,bar,bui,children,icon,iui,callback,button,
                    button_callback,go,visible,rect,method,button_method)=binding
                return (m.ref(bar_widget,'ui')==ui and m.ref(ui,'tracking_common_list')==group
                    and m.list_refs(m.ref(group,'_children'))==group_children
                    and m.ref(bar,'ui')==bui and self.integer(bar,'active',2,'B',1)==1
                    and m.list_refs(m.ref(m.ref(bui,'cells_list'),'_children'))==children
                    and m.ref(icon,'ui')==iui and m.ref(icon,'_clickHandler')==callback
                    and m.ref(callback,'_target')==cell and m.ref(iui,'icon_btn')==button
                    and m.ref(button,'_clickHandler')==button_callback
                    and m.ref(button_callback,'_target')==icon
                    and m.ref(iui,'<gameObject>k__BackingField')==go and self.layout.active(go)
                    and m.ref(iui,'root_visible_go')==visible and self.layout.active(visible)
                    and self._hud_callback_method(callback,cell,
                        ('CookResultTrackCellModel','XDTGUI.Module.Track.Cells'),'TriggerOnClickByView')==method
                    and self._hud_callback_method(button_callback,icon,
                        ('CommonIcon','XDTGame.UI.Widget'),'OnIconClicked')==button_method
                    and self._rect(button,stack,panel,nodes,size)==rect)
            diagnostic['collect_widget_binding_stable']=all(collect_stable(b) for b in collect_bindings)
            diagnostic['binding_stable'] &= diagnostic['collect_widget_binding_stable']
        if len(matches)!=1 or not diagnostic['binding_stable']:
            raise CookingObservationUpdating('当前翻锅或收菜控件未唯一稳定绑定',diagnostic)
        return {'kind':kind,'rect':matches[0],'stove_identity':current['stove_identity'],
                'scope':current['scope'],'event_id':str(current['status_started'])}

    def start_diagnostics(self,size):
        """Best-effort observations only. Missing data never changes input policy."""
        from datetime import datetime
        m=self.ui.meta;n=self.layout.native
        def unavailable(reason):return {'state':'unavailable','reason':reason}
        def attempt(fn):
            try:return fn()
            except Exception as exc:return unavailable(type(exc).__name__+': '+str(exc))
        def describe(obj):
            if not obj:return None
            info=m.obj(obj)
            return {'object':obj,'type':info['name'],'namespace':info['namespace']}
        def callback(button):
            cb=m.ref(button,'_clickHandler')
            if not cb:return {'delegate':None,'target':None,'method':None}
            method=self.integer(cb,'method',24,'Q',8)
            raw=m.data(method,32);ci=m.cls(struct.unpack_from('<Q',raw,8)[0])
            result={'delegate':describe(cb),'target':describe(m.ref(cb,'_target')),
                'method':{'pointer':method,'name':m.text(struct.unpack_from('<Q',raw,24)[0]),
                    'owner_type':ci['name'],'namespace':ci['namespace'],
                    'token':struct.unpack_from('<I',raw,4)[0]},
                'multicast':attempt(lambda:m.ref(cb,'delegates'))}
            if self.integer(cb,'method',24,'Q',8)!=method:
                return unavailable('callback changed during read')
            return result
        result={'observed_at':datetime.now().astimezone().isoformat(),
            'native_state':attempt(self.state),'menu':attempt(self.snapshot),
            'selected_recipe':attempt(lambda:self.recipe.snapshot().get('selected')),
            'cookpanel_onclickconfirm_executed':unavailable('binding and button timestamp do not prove callback execution'),
            'start_conditions_accepted':unavailable('no verified read-only acceptance field'),
            'start_rejection':unavailable('no verified read-only rejection/toast field'),
            'unity_button_received_click':unavailable('no direct Unity event execution trace'),
            'blocking_ui_confirmed':unavailable('UI stack is not a raycast/occlusion proof')}
        stack=attempt(self.ui.ui_views)
        if isinstance(stack,dict):result['ui_stack']=stack;return result
        result['ui_stack']=attempt(lambda:[describe(p) for p in stack[3]])
        result['possible_other_ui']=attempt(lambda:[describe(p) for p in stack[3]
            if m.obj(p)['name'] not in ('CookPanel','TrackingPanel')])
        witness=getattr(self,'start_button_witness',None)
        if witness is None:
            result['button']=unavailable('no start button witness');return result
        panel,nodes,button=witness
        result.update(panel=describe(panel),button=describe(button))
        result['panel_in_stack']=panel in stack[3]
        result['panel_display_cache']=attempt(lambda:self.integer(panel,'_viewActiveCache',2,'B',1))
        result['panel_focus_cache']=attempt(lambda:self.integer(panel,'_isGotFocus',2,'B',1))
        result['panel_roots']= {key:attempt(lambda key=key:self.layout.active(m.ref(nodes,key)))
            for key in ('background_go','list_go','btnBar_go')}
        result['button_binding_stable']=attempt(lambda:m.ref(panel,'ui')==nodes and m.ref(nodes,'confirm_swapbtn')==button)
        result['callback']=attempt(lambda:callback(button))
        result['last_click_time']=attempt(lambda:self.integer(button,'_lastClickTime',12,'f',4))
        result['rect']=attempt(lambda:self._rect(button,stack,panel,nodes,size))
        if isinstance(result['rect'],(tuple,list)):
            l,t,r,b=result['rect'];result['center']=(round((l+r)/2),round((t+b)/2))
        def native_button():
            obj=self.layout.resolve(m.ref(button,'unityComponent'),'Button','UnityEngine.UI')
            def flag(key):
                f=n.field(n.obj(obj)['address'],key,2)
                return n.data(obj+f['offset'],1)==b'\x01'
            return {'object':obj,**{key:attempt(lambda key=key:flag(key))
                for key in ('m_Enabled','m_Interactable','m_GroupsAllowInteraction')}}
        result['unity_button']=attempt(native_button)
        return result

    def observe_start_owner(self, observation, state, stack, panel, nodes, opened):
        m=self.ui.meta
        def optional(field, read):
            try:
                observation[field]=read()
            except Exception as exc:
                observation[field]={'unavailable':type(exc).__name__, 'message':str(exc)}
        def layers():
            return [layer for layer in m.dictionary(stack[2])
                    if panel in m.list_refs(m.ref(layer,'stack'))]
        observation.update(scene_scope=state['scope'], stove_identity=state['stove_identity'],
                           ui_manager=stack[1], ui_layer_dictionary=stack[2], ui_views=stack[3],
                           panel_address=panel, panel_nodes=nodes, picker_open=opened,
                           picker_closed=None, picker_state_verified=False,
                           picker_focus={'unavailable':'no verified focus field for the picker'},
                           picker_occlusion={'unavailable':'no verified raycast or occlusion data'})
        optional('panel_type',lambda: f"{m.obj(panel)['namespace']}.{m.obj(panel)['name']}")
        optional('panel_layers',layers)
        optional('panel_active',lambda:self.integer(panel,'_viewActiveCache',2,'B',1))
        optional('panel_focus',lambda:self.integer(panel,'_isGotFocus',2,'B',1))
        optional('picker_address',lambda:m.ref(nodes,'bag_widget'))
        picker=observation['picker_address']
        observation['picker_panel_in_stack']=picker in stack[3] if type(picker) is int else None
        observation.update(picker_root_present=None, picker_root_active=None, picker_view_active=None)
        if type(picker) is int and picker:
            optional('picker_root',lambda:m.ref(m.ref(picker,'ui'),'<gameObject>k__BackingField'))
            root=observation['picker_root']
            if type(root) is int:
                observation['picker_root_present']=bool(root)
                if root:
                    optional('picker_root_active',lambda:self.layout.active(root))
            if observation['picker_panel_in_stack']:
                optional('picker_view_active',lambda:self.integer(picker,'_viewActiveCache',2,'B',1))
                optional('picker_focus',lambda:self.integer(picker,'_isGotFocus',2,'B',1))
        observation['picker_active']=observation['picker_root_active']
        observation['picker_exists']=bool(picker) if type(picker) is int else None
        in_stack=observation['picker_panel_in_stack']
        if type(observation['picker_root_active']) is bool and (
                not in_stack or observation['picker_view_active'] in (0,1)):
            observation['picker_state_verified']=True
            observation['picker_closed']=not opened and not in_stack and not observation['picker_root_active']
        optional('button_bar_active',lambda:self.layout.active(m.ref(nodes,'btnBar_go')))

    def start_button_enabled(self, wrapper, address):
        """Read only a verified metadata field. Behaviour.enabled may be native-only."""
        native = self.layout.native
        expected = ('Button', 'UnityEngine.UI')
        try:
            if self.layout.resolve(wrapper, *expected) != address:
                raise ValueError('开始按钮原生引用变化')
            info = native.obj(address, expected)
            field = native.field(info['address'], 'm_Enabled', 2)
            offset = field['offset']
            if not 16 <= offset < info['size']:
                raise ValueError('开始按钮启用字段偏移超出对象范围')
            raw = native.data(address + offset, 1)
            if raw not in (b'\0', b'\1'):
                raise ValueError('开始按钮启用字段不是有效布尔值')
            if self.layout.resolve(wrapper, *expected) != address:
                raise ValueError('开始按钮原生引用变化')
        except (ValueError, OSError) as exc:
            return {'state': 'unknown', 'source': 'm_Enabled',
                    'error': {'type': type(exc).__name__, 'message': str(exc)}}
        return {'state': 'enabled' if raw == b'\1' else 'disabled', 'source': 'm_Enabled'}

    def menu_target(self, size, kind, *, index=None, net_id=None, slot_observation=None,
                    material_observation=None, start_observation=None):
        state,stack,panel,nodes=self.menu_owner(); m=self.ui.meta
        result={'kind':kind,'scope':state['scope'],'stove_identity':state['stove_identity']}
        opened=self.integer(panel,'_isOpenBag',2,'B',1)==1
        if kind=='start':
            if start_observation is not None:
                self.observe_start_owner(start_observation,state,stack,panel,nodes,opened)
            if opened and start_observation is None:raise ValueError('材料选择器尚未关闭，未点击开始')
            button=m.ref(nodes,'confirm_swapbtn')
            if start_observation is not None:
                start_observation.update(button_address=button, button_panel=panel,
                    button_parent_address={'unavailable':'transform chain exposes TRS, not parent addresses'})
                try:
                    game_object=m.ref(button,'<gameObject>k__BackingField')
                    start_observation['button_game_object']=game_object
                    start_observation['button_active']=self.layout.active(game_object)
                except Exception as exc:
                    unavailable={'unavailable':type(exc).__name__, 'message':str(exc)}
                    start_observation.setdefault('button_game_object',unavailable)
                    start_observation['button_active']=unavailable
            result['rect']=self._rect(button,stack,panel,nodes,size,start_observation)
            if start_observation is not None:
                start_observation['button_enabled']=self.start_button_enabled(
                    start_observation['button_wrapper'], start_observation['button_native'])
                selected=self.recipe.snapshot()['selected']
                start_observation['base_recipe_id']=selected['recipe_id'] if selected else None
                if m.ref(panel,'ui')!=nodes or m.ref(nodes,'confirm_swapbtn')!=button:
                    raise ValueError('开始按钮所属面板在读取期间变化')
            # Diagnostic witness only; never participates in click permission.
            self.start_button_witness=(panel,nodes,button)
        elif kind=='slot':
            group=m.ref(nodes,'material_list')
            children=m.ref(group,'_children')
            widgets=m.list_refs(children)
            materials = self.materials()
            if type(index) is not int or not 0<=index<len(materials['slots']) or index>=len(widgets):
                raise ValueError('材料槽控件没有对应')
            result.update(index=index, widget=widgets[index],
                          recipe_id=materials['selected']['recipe_id'], slot=materials['slots'][index])
            if slot_observation is not None:
                result['panel_binding']=(panel,nodes,opened,materials['selected_slot'])
                slot_observation.update(object_address=widgets[index], binding_identity=dict(result),
                                        selected_material_id=result['slot']['item_id'])
            result['rect']=self._toggle_rect(widgets[index],stack,size,slot_observation)
            if slot_observation is not None:
                result['component_binding']={key:slot_observation[key] for key in (
                    'rect_transform','toggle_address','native_toggle_address','widget_ui_address','game_object_address')}
                result['canvas_address']=slot_observation['canvas_address']
                if m.ref(nodes,'material_list')!=group or m.ref(group,'_children')!=children \
                        or m.list_refs(children)!=widgets:
                    raise ValueError('材料槽列表在读取期间变化')
        elif kind in ('material','close_picker'):
            if not opened:raise ValueError('材料选择器尚未打开')
            bag=m.ref(nodes,'bag_widget'); ui=m.ref(bag,'ui')
            if kind=='close_picker':
                result['rect']=self._rect(m.ref(ui,'close_btn'),stack,panel,nodes,size)
            else:
                options=m.ref(bag,'_opt')
                recipe_id=self.integer(options,'RecipeId')
                if recipe_id!=self.materials()['selected']['recipe_id']:
                    raise ValueError('材料选择器绑定的料理或槽位不符')
                selected_slot=self.integer(options,'SelectedSlotIndex')
                if selected_slot!=index or self.integer(panel,'_selectedSlotIndex')!=index:
                    raise ValueError('材料选择器绑定的料理或槽位不符')
                data_list=m.ref(bag,'_bagList');data=m.list_refs(data_list);group=m.ref(ui,'pack_list')
                children=m.ref(group,'_children');widgets=m.list_refs(children)
                if material_observation is not None:
                    material_observation.update(panel=panel, bag=bag, bag_ui=ui, options=options,
                        recipe_id=recipe_id, selected_slot=selected_slot, pack_list=group,
                        data_list=data_list, children_list=children, data_count=len(data), widget_count=len(widgets))
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
                if material_observation is not None:
                    material_observation.update(row_index=i, row_address=data[i], widget_address=widgets[i],
                                                item_id=item_id, net_id=net_id)
                result.update(index=index,net_id=net_id,item_id=item_id,
                              rect=self._toggle_rect(widgets[i],stack,size,material_observation,
                                                     material_candidate=material_observation is not None))
                viewport=m.ref(group,'_viewPort')
                if material_observation is not None:
                    material_observation['viewport_address']=viewport
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
