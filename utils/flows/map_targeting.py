"""Set a fixed native map target through owned UI, then verify native readback.

Runs only inside a user-started task. No process writes or game API invocation;
all input goes through the existing injectable device and stop/focus controls.
"""
import math
from dataclasses import replace,asdict

from utils.core import thresholds as policy
from utils.core.map_target_policy import MapTargetPolicy
from utils.core.map_targeting import plan_map_click,plan_map_pan,confirm_map_target,blank_map_points
from utils.core.map_targeting import MapLayoutUpdating
from utils.drivers.guidance_input_lease import GuidanceInputLease
from utils.drivers.map_panel import MapPanelReader


class _MapActionTimeout(ValueError):
    pass


class _MapInputRevoked(ValueError):
    def __init__(self,reason):
        self.reason=reason
        super().__init__(f'地图输入许可已撤销（{reason}），等待重新核对')


class MapTargeter:
    def __init__(self,reader,device,window,controls,clock,check,report,*,panel=None,options=None):
        self.reader,self.device,self.window,self.controls=reader,device,window,controls
        self.clock,self.check,self.report=clock,check,report
        self.panel=panel or MapPanelReader(reader,clock.monotonic)
        self.options=options or MapTargetPolicy()
        self.size=tuple(window.size())
        self.lease=None
        self.started=0.
        self.phase='地图准备'
        self.drag_retries=0
        self.map_scope=None

    def stage(self,name,**extra):
        self.phase=name
        if self.lease:self.lease.set_phase(name)
        self.report('map_targeting',name,map_stage=name,**extra)

    def guard(self):
        self.check()
        if self.clock.monotonic()-self.started>self.options.setup_timeout:
            raise ValueError('自动地图设标超过本次时限，未开始行走')
        if not self.window.foreground() or tuple(self.window.size())!=self.size:
            raise ValueError('地图设标时游戏失焦或尺寸改变，已释放输入')
        if self.lease:
            self.lease.poll()
            if self.lease.error:
                raise OSError('地图输入释放失败') from self.lease.error
            if self.lease.tripped:
                reason=self.lease.revocation_reason or 'unknown'
                self.report('map_targeting','地图输入许可已撤销',map_stage=self.phase,
                    map_revoke_reason=reason,map_input_lease=self.lease.metrics())
                raise _MapInputRevoked(reason)

    def start_lease(self):
        self.lease=GuidanceInputLease(self.device,self.controls,self.window,list(self.size),self.clock.monotonic)
        self.lease.start()

    def recover_drag(self,view,target):
        # The old watchdog has already released the gesture. Never resume its
        # button-down: retire it, verify live UI without input, then replan.
        self.lease.close();self.lease=None
        self.guard()
        first=self.panel.geometry(self.size)
        self.wait(self.options.settle_seconds)
        current=self.panel.geometry(self.size)
        current.validate(self.clock.monotonic(),self.options.view_max_age)
        if first.signature()!=current.signature() \
                or replace(current,pan=view.pan).signature()!=view.signature():
            raise ValueError('拖动中断后地图场景或视图已改变，未恢复输入')
        l,t,r,b=view.map_rect;center=((l+r)/2,(t+b)/2)
        if math.dist(current.world_to_client(target),center)>math.dist(view.world_to_client(target),center)+.5:
            raise ValueError('中断的地图拖动远离目标，未恢复输入')
        if any(self.panel.dialog(self.size,kind)['state']=='open' for kind in ('add','spot')):
            raise ValueError('拖动中断后出现标记弹窗，未恢复输入')
        self.guard()
        self.drag_retries+=1
        self.start_lease()
        self.stage('重新核对后恢复地图平移',map_drag_retry=self.drag_retries,map_pan=list(current.pan))

    def wait(self,seconds):
        end=self.clock.monotonic()+seconds
        while self.clock.monotonic()<end:
            self.guard()
            if self.device.keys or self.device.buttons:
                with self.lease.lock:
                    self.lease.arm(self.clock.monotonic()+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS)
            self.clock.sleep(min(.01,max(0.,end-self.clock.monotonic())))
        self.guard()

    def poll(self,read,accept):
        start=self.clock.monotonic();end=start+self.options.action_timeout;error=None;observed=None
        while self.clock.monotonic()<end:
            self.guard()
            try:
                value=read()
                observed=({k:value[k] for k in ('state','kind','usage_id','category','fraction') if k in value}
                    if isinstance(value,dict) else {'scale':value.scale} if hasattr(value,'scale') else None)
                if accept(value):return value
            except (ValueError,OSError) as exc:error=exc
            self.wait(self.options.settle_seconds)
        self.report('map_targeting',f'{self.phase}未确认',map_stage=self.phase,
            map_observed=observed,map_read_error=str(error) if error else None,
            map_wait_seconds=self.clock.monotonic()-start)
        raise _MapActionTimeout(f'{self.phase}未确认：{error or "界面尚未达到预期状态"}；最近读数 {observed}')

    def tap(self,key):
        self.guard()
        with self.lease.lock:
            self.lease.arm(self.clock.monotonic()+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS)
            self.device.execute({'type':'key_down','key':key})
        try:self.wait(self.options.press_seconds)
        finally:self.lease.release('map_key_complete')

    def read_layout(self,read):
        """Retry only torn layout values, without repeating any UI input."""
        start=self.clock.monotonic();retries=0;scope=None
        def check_scope():
            current=self.panel.snapshot()
            if current.get('state')!='projection_unverified' or (
                    current.get('scene_context'),current.get('view_identity'))!=scope:
                raise ValueError('等待布局期间地图场景或面板已改变，未继续操作')
        while True:
            self.guard()
            try:
                value=read()
                if scope:check_scope()
                if retries:self.report('map_targeting','地图布局已稳定',map_stage=self.phase,
                    map_layout_retries=retries,map_layout_wait_seconds=self.clock.monotonic()-start)
                return value
            except MapLayoutUpdating as exc:
                pending=exc.scope or self.map_scope
                if pending is None or (scope is not None and pending!=scope) \
                        or (self.map_scope is not None and pending!=self.map_scope):
                    raise ValueError('地图布局更新归属未确认，未继续操作') from exc
                scope=pending;check_scope()
                if self.device.keys or self.device.buttons:
                    raise ValueError('持有输入时不等待地图布局') from exc
                if self.clock.monotonic()-start>=self.options.action_timeout:
                    raise _MapActionTimeout('等待地图布局稳定超过本次时限，未继续操作') from exc
                retries+=1
                if retries==1:self.report('map_targeting','等待地图布局稳定',map_stage=self.phase,
                    map_layout_reason=str(exc),map_layout_retries=retries)
                self.wait(self.options.settle_seconds)

    def stable_views(self,scope=None):
        previous=None;expected=scope or self.map_scope
        def read():
            nonlocal previous,expected
            current=self.panel.geometry(self.size)
            current.validate(self.clock.monotonic(),self.options.view_max_age)
            observed=(current.scene,current.identity)
            if expected is not None and observed!=expected:
                raise ValueError('等待布局期间地图场景或面板已改变，未继续操作')
            expected=observed;before=previous;previous=current
            if before is not None and before.signature()==current.signature() and (
                    0<current.captured_at-before.captured_at<=self.options.view_max_age):
                before.validate(self.clock.monotonic(),self.options.view_max_age)
                return before,current
            raise MapLayoutUpdating('等待两份一致的新鲜地图布局',scope=expected)
        return self.read_layout(read)

    def position_cursor(self,pixel):
        self.guard();x,y=(round(v) for v in pixel)
        if not 0<x<self.size[0]-1 or not 0<y<self.size[1]-1:
            raise ValueError('地图点击目标在客户区外')
        self.device.execute({'type':'cursor_move','x':x,'y':y})
        self.guard()
        pointer=self.window.pointer()
        if len(pointer)!=3 or not pointer[2] or math.dist(pointer[:2],(x,y))>2.:
            raise ValueError('游戏鼠标未到达地图目标位置，未按下鼠标')

    def click(self,pixel,hold=None):
        self.position_cursor(pixel)
        with self.lease.lock:
            self.lease.arm(self.clock.monotonic()+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS)
            self.device.execute({'type':'mouse_down','button':'left'})
        try:self.wait(self.options.press_seconds if hold is None else hold)
        finally:self.lease.release('map_click_complete')

    def click_button(self,dialog,name):
        if self.clock.monotonic()-dialog['captured_at']>self.options.view_max_age:
            raise ValueError('地图按钮读数已过期')
        fresh=(self.panel.zoom_state(self.size) if dialog['kind']=='map_zoom' else self.panel.map_control(self.size,dialog['kind'].removeprefix('map_'))
            if dialog['kind'] in ('map_open','map_close') else self.panel.dialog(self.size,dialog['kind']))
        fields=('state','kind','usage_id','category','scene_context','view_identity','position_xz','fraction')
        if any(fresh.get(k)!=dialog.get(k) for k in fields) \
                or fresh.get('buttons',{}).get(name)!=dialog['buttons'][name]:
            raise ValueError('地图按钮或其目标在点击前发生变化')
        l,t,r,b=dialog['buttons'][name]
        self.click(((l+r)/2,(t+b)/2))

    def change_map(self,opened):
        action='open' if opened else 'close'
        self.stage('打开大地图' if opened else '关闭大地图')
        expected='projection_unverified' if opened else 'closed'
        self.guard()
        if self.panel.snapshot()['state']==expected:return
        if opened:
            # In mouse-look mode the HUD cursor is hidden. Open the map with
            # its shortcut first; cursor checks belong to the opened map.
            # M is only used to open, never assumed to toggle the map closed.
            self.tap('m')
            self.poll(self.panel.snapshot,lambda v:v['state']==expected)
            self.wait(self.options.settle_seconds)
            return
        button=self.panel.map_control(self.size,action)
        if button.get('state')!='open':
            if self.panel.snapshot()['state']==expected:return
            raise ValueError(f'{self.phase}按钮尚未确认')
        self.click_button(button,action)
        self.poll(self.panel.snapshot,lambda v:v['state']==expected)

    def dismiss_popups(self):
        state=self.read_layout(lambda:self.panel.overlays(self.size))
        if not state['popups']:return
        self.stage('点击地图空白处收起弹窗',map_popups=list(state['popups']))
        for attempt in range(3):
            state=self.read_layout(lambda:self.panel.overlays(self.size))
            if not state['popups']:return
            points=state['blank_points']
            if len(points)<=attempt:raise ValueError('没有确认可点击的地图空白区域，未关闭弹窗')
            self.click(points[attempt])
            try:
                self.poll(lambda:self.panel.overlays(self.size),lambda s:not s['popups'])
                return
            except _MapActionTimeout:pass
        raise ValueError('地图弹窗仍未收起，未继续设标')

    def minimize(self):
        self.dismiss_popups()
        self.stage('缩放地图至最小')
        read=lambda:self.panel.zoom_state(self.size,include_button=False)
        for step in range(self.options.max_zoom_steps+1):
            state=read()
            if state['fraction']<=1e-6:return
            if step==self.options.max_zoom_steps:break
            a,b=self.stable_views((state['scene_context'],state['view_identity']))
            a.validate(self.clock.monotonic(),self.options.view_max_age)
            b.validate(self.clock.monotonic(),self.options.view_max_age)
            if a.signature()!=b.signature() or b.scene!=state['scene_context'] \
                    or b.identity!=state['view_identity']:
                raise ValueError('缩放前地图视图发生变化，未滚动')
            points=blank_map_points(b,(),self.clock.monotonic(),max_age=self.options.view_max_age,
                                    margin=self.options.pixel_margin)
            if not points:raise ValueError('没有确认可滚动的地图操作区域')
            l,t,r,bot=b.map_rect;center=((l+r)/2,(t+bot)/2)
            pixel=min(points,key=lambda point:math.dist(point,center))
            self.position_cursor(pixel)
            if self.panel.geometry(self.size).signature()!=b.signature():
                raise ValueError('鼠标到位后地图视图发生变化，未滚动')
            self.guard()
            self.report('map_targeting','在地图区域滚轮缩小',map_stage=self.phase,
                        map_zoom_fraction=state['fraction'],map_zoom_pixel=list(pixel))
            # Negative wheel input shrinks via the map's native zoom handler.
            # No held input or small button-center assumption is required.
            self.device.execute({'type':'scroll','dx':0,'dy':-12})
            self.poll(read,lambda s:s['fraction']<state['fraction'])
            self.stage('等待地图缩放布局稳定')
            self.stable_views((state['scene_context'],state['view_identity']))
            self.dismiss_popups()
            self.stage('缩放地图至最小')
        raise ValueError('地图最低缩放未确认，未平移或设标')

    def view_changes(self,before,after):
        a,b=asdict(before),asdict(after)
        changes={k:{'before':a[k],'after':b[k]} for k in a if k not in ('captured_at','pan') and a[k]!=b[k]}
        self.report('map_targeting','地图视图变化',map_stage=self.phase,map_view_changes=changes)
        return ','.join(changes)

    def pan(self,target,view):
        self.stage('平移地图',target=list(target),map_pan=list(view.pan))
        start,end=plan_map_pan(target,view,self.clock.monotonic(),
            max_age=self.options.view_max_age,margin=self.options.pixel_margin)
        current=self.panel.geometry(self.size)
        if current.signature()!=view.signature():
            raise ValueError('拖动前地图视图已经变化')
        self.position_cursor(start)
        with self.lease.lock:
            self.lease.arm(self.clock.monotonic()+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS)
            self.device.execute({'type':'mouse_down','button':'left'})
        try:
            self.wait(self.options.press_seconds)
            for step in range(1,self.options.drag_steps+1):
                fraction=step/self.options.drag_steps
                self.position_cursor(tuple(a+(b-a)*fraction for a,b in zip(start,end)))
                self.wait(self.options.drag_seconds/self.options.drag_steps)
        finally:self.lease.release('map_drag_complete')
        self.dismiss_popups()
        self.stage('平移地图后核对',target=list(target))
        try:
            current=self.poll(lambda:self.panel.geometry(self.size),lambda v:v.pan!=view.pan)
        except _MapActionTimeout:
            current=self.panel.geometry(self.size)
        current.validate(self.clock.monotonic(),self.options.view_max_age)
        if replace(current,pan=view.pan).signature()!=view.signature():
            raise ValueError('地图平移时视图发生变化：'+self.view_changes(view,current))
        l,t,r,b=view.map_rect;center=((l+r)/2,(t+b)/2)
        before=math.dist(view.world_to_client(target),center)
        after=math.dist(current.world_to_client(target),center)
        if after>before+.5:
            raise ValueError('地图平移方向未接近目标，未点击或保存标记')
        return after<before-.5

    def prepare(self,target):
        self.minimize()
        self.stage('校准地图视图',target=list(target))
        pans=0
        for _ in range(self.options.max_pan_steps+1):
            self.dismiss_popups()
            a,b=self.stable_views()
            try:
                return plan_map_click(target,a,b,self.clock.monotonic(),
                    max_age=self.options.view_max_age,margin=self.options.pixel_margin)
            except ValueError:
                a.validate(self.clock.monotonic(),self.options.view_max_age)
                b.validate(self.clock.monotonic(),self.options.view_max_age)
                if a.signature()!=b.signature():
                    raise ValueError('地图视图正在变化，未发送缩放输入')
                pixel=b.world_to_client(target);l,t,r,bot=b.map_rect
                if l+self.options.pixel_margin<pixel[0]<r-self.options.pixel_margin \
                        and t+self.options.pixel_margin<pixel[1]<bot-self.options.pixel_margin:
                    raise  # Do not call a changed/invalid view an out-of-view target.
                if pans<self.options.max_pan_steps:
                    pans+=1
                    try:
                        if self.pan(target,b):continue
                    except _MapInputRevoked as exc:
                        if exc.reason!='lease_expired' or self.drag_retries>=self.options.max_drag_retries:
                            raise
                        self.recover_drag(b,target)
                        continue
                    pans=self.options.max_pan_steps  # Already at minimum: never drag indefinitely.
                raise ValueError('最小缩放下目标仍未进入可操作区域，未继续设标')
        raise ValueError('地图目标无法投影到可操作区域')

    def set_target(self,target):
        self.started=self.clock.monotonic()
        self.drag_retries=0
        self.map_scope=None
        self.device.release_all();self.start_lease()
        try:
            self.stage('检查地图状态',target=list(target))
            self.guard();state=self.panel.snapshot()
            if state['state']=='closed':
                self.change_map(True)
            anchor=self.panel.snapshot()
            if anchor.get('state')!='projection_unverified':raise ValueError('当前地图未展开')
            self.map_scope=(anchor.get('scene_context'),anchor.get('view_identity'))
            if not all(isinstance(v,str) and v for v in self.map_scope):
                raise ValueError('地图场景或面板身份未确认')
            self.dismiss_popups()
            plan=self.prepare(target);markers=self.panel.custom_markers()
            nearby=[(math.hypot(p[0]-target[0],p[2]-target[2]),i) for i,p in markers.items()
                    if math.hypot(p[0]-target[0],p[2]-target[2])<=self.options.world_tolerance]
            if nearby:
                marker_id=min(nearby)[1]
            else:
                self.stage('长按添加标记',target=list(target))
                self.report('map_targeting','正在为当前采集点设置游戏地图标记',target=list(target))
                self.click(plan.pixel,self.options.hold_seconds)
                dialog=self.poll(lambda:self.panel.dialog(self.size),lambda v:v['state']=='open')
                if dialog['usage_id']!='0' or dialog['scene_context']!=plan.scene \
                        or math.dist(dialog['position_xz'],(target[0],target[2]))>self.options.world_tolerance:
                    raise ValueError('长按读回位置与采集点不符，未保存标记')
                self.stage('保存标记')
                self.click_button(dialog,'save')
                def created(values):
                    candidates=[i for i,p in values.items() if i not in markers
                        and math.hypot(p[0]-target[0],p[2]-target[2])<=self.options.world_tolerance]
                    return candidates[0] if len(candidates)==1 else None
                values=self.poll(self.panel.custom_markers,lambda v:created(v) is not None)
                marker_id=created(values)
            # Reproject after a server/UI update, rather than reusing old pixels.
            plan=self.prepare(target);self.stage('选择目标标记',map_marker_id=marker_id);self.click(plan.pixel)
            selected=self.poll(lambda:self.panel.dialog(self.size,'spot'),lambda v:v['state']=='open')
            if selected['category']!=7 or selected['usage_id']!=marker_id:
                raise ValueError('地图选中的不是本次目标标记，未开始追踪')
            requested_at=self.clock.monotonic()
            def confirm():
                a=self.reader.guidance();self.wait(self.options.settle_seconds);b=self.reader.guidance()
                return confirm_map_target(plan,a,b,self.clock.monotonic(),requested_at=requested_at,
                                          world_tolerance=self.options.world_tolerance)
            try:
                result=confirm()  # Reuse a live matching track, do not toggle it off.
            except (ValueError,OSError):
                requested_at=self.clock.monotonic()
                selected=self.panel.dialog(self.size,'spot')
                if selected.get('usage_id')!=marker_id or selected.get('category')!=7:
                    raise ValueError('追踪前标记身份已改变')
                self.stage('追踪目标',map_marker_id=marker_id)
                self.click_button(selected,'track')
                result=self.poll(confirm,lambda v:bool(v.get('route')))
            self.change_map(False)
            result['map_marker_id']=marker_id
            self.report('map_target_ready','游戏引导目标已核对',target=list(target),
                map_marker_id=marker_id,tracking_id=result['tracking_id'],path_end_gap=result['path_end_gap'])
            return result
        finally:
            if self.lease:self.lease.close()
            self.lease=None
