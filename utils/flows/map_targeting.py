"""Set a fixed native map target through owned UI, then verify native readback.

Runs only inside a user-started task. No process writes or game API invocation;
all input goes through the existing injectable device and stop/focus controls.
"""
import math

from utils.core import thresholds as policy
from utils.core.map_target_policy import MapTargetPolicy
from utils.core.map_targeting import plan_map_click,confirm_map_target
from utils.drivers.guidance_input_lease import GuidanceInputLease
from utils.drivers.map_panel import MapPanelReader


class MapTargeter:
    def __init__(self,reader,device,window,controls,clock,check,report,*,panel=None,options=None):
        self.reader,self.device,self.window,self.controls=reader,device,window,controls
        self.clock,self.check,self.report=clock,check,report
        self.panel=panel or MapPanelReader(reader,clock.monotonic)
        self.options=options or MapTargetPolicy()
        self.size=tuple(window.size())
        self.lease=None
        self.started=0.

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
                raise ValueError('地图输入许可已撤销，等待重新核对')

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
        end=self.clock.monotonic()+self.options.action_timeout;error=None
        while self.clock.monotonic()<end:
            self.guard()
            try:
                value=read()
                if accept(value):return value
            except (ValueError,OSError) as exc:error=exc
            self.wait(self.options.settle_seconds)
        raise ValueError(f'地图操作未确认：{error or "界面尚未达到预期状态"}')

    def tap(self,key):
        self.guard()
        with self.lease.lock:
            self.lease.arm(self.clock.monotonic()+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS)
            self.device.execute({'type':'key_down','key':key})
        try:self.wait(self.options.press_seconds)
        finally:self.lease.release('map_key_complete')

    def click(self,pixel,hold=None):
        self.guard();x,y=(round(v) for v in pixel)
        if not 0<x<self.size[0]-1 or not 0<y<self.size[1]-1:
            raise ValueError('地图点击目标在客户区外')
        self.device.execute({'type':'cursor_move','x':x,'y':y})
        self.guard()
        pointer=self.window.pointer()
        if len(pointer)!=3 or not pointer[2] or math.dist(pointer[:2],(x,y))>2.:
            raise ValueError('游戏鼠标未到达地图目标位置，未按下鼠标')
        with self.lease.lock:
            self.lease.arm(self.clock.monotonic()+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS)
            self.device.execute({'type':'mouse_down','button':'left'})
        try:self.wait(self.options.press_seconds if hold is None else hold)
        finally:self.lease.release('map_click_complete')

    def click_button(self,dialog,name):
        if self.clock.monotonic()-dialog['captured_at']>self.options.view_max_age:
            raise ValueError('地图按钮读数已过期')
        fresh=self.panel.dialog(self.size,dialog['kind'])
        fields=('state','kind','usage_id','category','scene_context','view_identity','position_xz')
        if any(fresh.get(k)!=dialog.get(k) for k in fields) \
                or fresh.get('buttons',{}).get(name)!=dialog['buttons'][name]:
            raise ValueError('地图按钮或其目标在点击前发生变化')
        l,t,r,b=dialog['buttons'][name]
        self.click(((l+r)/2,(t+b)/2))

    def prepare(self,target):
        for attempt in range(self.options.max_zoom_steps+1):
            a=self.panel.geometry(self.size);self.wait(self.options.settle_seconds)
            b=self.panel.geometry(self.size)
            try:
                return plan_map_click(target,a,b,self.clock.monotonic(),
                    max_age=self.options.view_max_age,margin=self.options.pixel_margin)
            except ValueError:
                pixel=b.world_to_client(target);l,t,r,bot=b.map_rect
                if l+self.options.pixel_margin<pixel[0]<r-self.options.pixel_margin \
                        and t+self.options.pixel_margin<pixel[1]<bot-self.options.pixel_margin:
                    raise  # Do not call a changed/invalid view an out-of-view target.
                if attempt==self.options.max_zoom_steps:raise
                self.guard();self.device.execute({'type':'scroll','dx':0,'dy':-1})
                self.poll(lambda:self.panel.geometry(self.size),lambda v:v.scale<a.scale)
        raise ValueError('地图目标无法投影到可操作区域')

    def set_target(self,target):
        self.started=self.clock.monotonic()
        self.lease=GuidanceInputLease(self.device,self.controls,self.window,list(self.size),self.clock.monotonic)
        self.device.release_all();self.lease.start()
        try:
            self.guard();state=self.panel.snapshot()
            if state['state']=='closed':
                self.tap('m')
                self.poll(self.panel.snapshot,lambda v:v['state']=='projection_unverified')
            existing=self.panel.dialog(self.size)
            if existing['state']=='open':
                raise ValueError('地图有尚未保存的标记编辑，请先关闭该弹窗')
            if self.panel.dialog(self.size,'spot')['state']=='open':
                # Dismiss an existing popup without clicking through any of
                # its buttons (which can include deletion or teleportation).
                self.tap('m');self.poll(self.panel.snapshot,lambda v:v['state']=='closed')
                self.tap('m');self.poll(self.panel.snapshot,lambda v:v['state']=='projection_unverified')
                if self.panel.dialog(self.size,'spot')['state']=='open':
                    raise ValueError('原地图标记弹窗尚未收起，未点击新位置')
            plan=self.prepare(target);markers=self.panel.custom_markers()
            nearby=[(math.hypot(p[0]-target[0],p[2]-target[2]),i) for i,p in markers.items()
                    if math.hypot(p[0]-target[0],p[2]-target[2])<=self.options.world_tolerance]
            if nearby:
                marker_id=min(nearby)[1]
            else:
                self.report('map_targeting','正在为当前采集点设置游戏地图标记',target=list(target))
                self.click(plan.pixel,self.options.hold_seconds)
                dialog=self.poll(lambda:self.panel.dialog(self.size),lambda v:v['state']=='open')
                if dialog['usage_id']!='0' or dialog['scene_context']!=plan.scene \
                        or math.dist(dialog['position_xz'],(target[0],target[2]))>self.options.world_tolerance:
                    raise ValueError('长按读回位置与采集点不符，未保存标记')
                self.click_button(dialog,'save')
                def created(values):
                    candidates=[i for i,p in values.items() if i not in markers
                        and math.hypot(p[0]-target[0],p[2]-target[2])<=self.options.world_tolerance]
                    return candidates[0] if len(candidates)==1 else None
                values=self.poll(self.panel.custom_markers,lambda v:created(v) is not None)
                marker_id=created(values)
            # Reproject after a server/UI update, rather than reusing old pixels.
            plan=self.prepare(target);self.click(plan.pixel)
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
                self.click_button(selected,'track')
                result=self.poll(confirm,lambda v:bool(v.get('route')))
            self.tap('m');self.poll(self.panel.snapshot,lambda v:v['state']=='closed')
            result['map_marker_id']=marker_id
            self.report('map_target_ready','游戏引导目标已核对',target=list(target),
                map_marker_id=marker_id,tracking_id=result['tracking_id'],path_end_gap=result['path_end_gap'])
            return result
        finally:
            self.lease.close();self.lease=None
