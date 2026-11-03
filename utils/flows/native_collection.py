"""Per saved point: verified map target, native guide, local finish, old harvest.

The outer collection state machine continues to own order, loops, inventory,
collection presses and waits. Guidance completion alone never harvests a point.
"""
import math

from utils.core import thresholds as policy
from utils.core.coordinate_route import distance
from utils.core.guidance_path import GuidanceRouteCache,validate_guidance_state
from utils.core.guidance_navigation import GuidanceNavigator
from utils.flows.coordinate_player import _Stopped
from utils.flows.collection_player import CollectionFollower
from utils.flows.map_targeting import MapTargeter
from utils.flows.native_guidance_follow import GuidanceFollower


class BoundGuidanceReader:
    def __init__(self,reader,prepared,target,scene_check,clock,tolerance):
        self.reader,self.target,self.scene_check,self.clock=reader,tuple(target),scene_check,clock
        self.scene=prepared['scene_context'];self.tracking_id=prepared['tracking_id']
        self.tolerance=tolerance

    def __getattr__(self,name):return getattr(self.reader,name)

    def _checked(self,value):
        if self.scene_check()!=self.scene:
            raise ValueError('采集引导所在场景已改变')
        if value.get('state')=='inactive':return value
        validate_guidance_state(value,self.clock())
        if value.get('scene_context')!=self.scene or value.get('tracking_id')!=self.tracking_id \
                or math.hypot(value['target'][0]-self.target[0],value['target'][2]-self.target[2])>self.tolerance:
            raise ValueError('当前游戏引导与本次采集点不符')
        return value

    def guidance(self):return self._checked(self.reader.guidance())

    def guidance_status(self):
        source=getattr(self.reader,'guidance_status',self.reader.guidance)
        return self._checked(source())


class NativeCollectionFollower(CollectionFollower):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.map_targeter=None
        self.native_leg=None
        self.native_follower=None

    def release(self,reason='controller'):
        # The inner guide must disarm its watchdog before the outer owner's
        # meal/tool code uses the shared device. Otherwise it can release a
        # menu click or retain a stale movement-key set after the menu closes.
        if self.native_follower is not None:
            self.native_follower.release(reason)
        super().release(reason)

    def native_maintenance(self,index):
        self.last_tick=self.clock.monotonic()
        self.travelling=False
        self.navigation_waiting=True
        try:
            return self.maintain_stamina(index,len(self.waypoints)) \
                or self.maintain_tool(index,len(self.waypoints))
        finally:
            self.navigation_waiting=False
            self.last_tick=self.clock.monotonic()

    def before_waypoint_navigation(self,index,target,position):
        token=(self.cycle,index,tuple(target))
        if self.native_leg==token or distance(position,target)<=policy.COORD_NAV_APPROACH_RADIUS:
            return False
        self.release('prepare_native_target')
        if self.map_targeter is None:
            self.map_targeter=MapTargeter(self.reader,self.device,self.window,self.controls,
                                         self.clock,self.guard,self.report)
        self.navigation_waiting=True
        try:
            accepted=self.map_targeter.set_target(target)
        finally:
            self.navigation_waiting=False;self.last_tick=self.clock.monotonic()
        prepared=GuidanceRouteCache();prepared.install(accepted['route'],self.clock.monotonic())
        bound=BoundGuidanceReader(self.reader,accepted['route'],target,
            self.map_targeter.panel.scene_context,self.clock.monotonic,self.map_targeter.options.world_tolerance)
        provider=self.navigator.native_scene_provider or self.navigator.scene_provider
        navigator=GuidanceNavigator(provider,clock=self.clock.monotonic,
            scene_clock=self.navigator.scene_clock,water_areas=self.navigator.water_areas,respect_water=False)
        def report(state,message,**extra):
            extra.update(waypoint=index+1,waypoint_count=len(self.waypoints),
                         navigation_mode='native_guidance_collection')
            self.report(state,message,**extra)
        follower=GuidanceFollower(self.data['client_size'],bound,self.device,self.window,
            self.controls,report,self.clock,navigator,prepared)
        follower.show_cycle_label=False  # Outer controller owns cycle labels.
        follower.maintenance_callback=lambda:self.native_maintenance(index)
        point_budget=max(.01,self.travel_progress.budget-self.travel_progress.elapsed)
        follower.budget=min(point_budget,max(.01,self.budget-self.active_time))
        try:
            self.native_follower=follower
            if follower.input_lease:follower.input_lease.start()
            result=follower.follow_guidance()
        finally:
            self.native_follower=None
            if follower.input_lease:follower.input_lease.close()
            self.device.release_all();self.keys.clear()
            self.active_time+=follower.active_time
            self.travel_progress.account(follower.active_time)
            self.last_tick=self.clock.monotonic()
            self.position_at=None
        if self.controls.snapshot()[1]:raise _Stopped()
        if self.map_targeter.panel.scene_context()!=accepted['route']['scene_context']:
            raise ValueError('游戏引导结束时场景已改变，未进行采集')
        current=self.read(force=True)
        ended=result.get('reason')=='游戏追踪已结束，已停止跟随'
        if not result.get('arrived') and not ended:
            raise ValueError('本采集点游戏引导未完成：'+result.get('reason','未知原因'))
        if distance(current,target)>policy.COORD_NAV_SEARCH_RADIUS:
            raise ValueError('游戏引导结束位置仍离采集点过远，未自动改用长距离直线')
        self.native_leg=token
        self.navigator.invalidate()
        self.report('navigating','游戏引导已接近，继续精确到达采集点',
            waypoint=index+1,waypoint_count=len(self.waypoints),
            native_guidance_end_distance=distance(current,target))
        return True
