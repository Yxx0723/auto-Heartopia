"""Pure map projection and readback contracts. Never sends game input.

View verification and the pixel/world tolerances belong to the calibrated
caller. A correct map marker is not collection arrival or loot confirmation.
"""
from dataclasses import dataclass
import math

from utils.core.coordinate_route import valid_position
from utils.core.guidance_path import validate_guidance


def _numbers(values, count):
    return isinstance(values, tuple) and len(values)==count and all(
        type(x) in (int,float) and math.isfinite(x) for x in values)


@dataclass(frozen=True)
class MapView:
    scene: str
    identity: str
    client_size: tuple
    canvas_size: tuple
    pan: tuple
    scale: float
    ratio: float
    captured_at: float
    opened: bool
    verified: bool
    map_rect: tuple
    blocked_rects: tuple = ()

    def validate(self, now, max_age):
        if not _numbers((now,max_age,self.captured_at),3) or max_age<=0 \
                or not 0<=now-self.captured_at<=max_age:
            raise ValueError('地图视图数据尚未更新')
        if self.opened is not True or self.verified is not True:
            raise ValueError('地图未打开或坐标映射尚未校验')
        if not isinstance(self.scene,str) or not self.scene or not isinstance(self.identity,str) or not self.identity:
            raise ValueError('地图场景身份未确认')
        if not _numbers(self.client_size,2) or min(self.client_size)<=0 \
                or not _numbers(self.canvas_size,2) or min(self.canvas_size)<=0 \
                or not _numbers(self.pan,2) or not _numbers((self.scale,self.ratio),2) \
                or self.scale<=0 or self.ratio<=0:
            raise ValueError('地图缩放或坐标参数无效')
        for rect in (self.map_rect,*self.blocked_rects):
            if not _numbers(rect,4) or not 0<=rect[0]<rect[2]<=self.client_size[0] \
                    or not 0<=rect[1]<rect[3]<=self.client_size[1]:
                raise ValueError('地图可点击区域未确认')

    def signature(self):
        return (self.scene,self.identity,self.client_size,self.canvas_size,self.pan,
                self.scale,self.ratio,self.map_rect,self.blocked_rects,self.opened,self.verified)

    def world_to_client(self, point):
        if not valid_position(point):
            raise ValueError('采集点坐标无效')
        w,h=self.client_size;cw,ch=self.canvas_size
        return (w/2+(self.pan[0]+point[0]*self.scale*self.ratio)*w/cw,
                h/2-(self.pan[1]+point[2]*self.scale*self.ratio)*h/ch)

    def client_to_world(self, pixel):
        w,h=self.client_size;cw,ch=self.canvas_size
        return (((pixel[0]-w/2)*cw/w-self.pan[0])/(self.scale*self.ratio),
                ((h/2-pixel[1])*ch/h-self.pan[1])/(self.scale*self.ratio))


@dataclass(frozen=True)
class MapClickPlan:
    target: tuple
    pixel: tuple
    scene: str
    view_signature: tuple
    prepared_at: float


def plan_map_click(target, first, second, now, *, max_age, margin):
    """Require two independent unchanged verified views; never clamp an aim."""
    if not _numbers((margin,),1) or margin<0:
        raise ValueError('地图点击边距无效')
    first.validate(now,max_age);second.validate(now,max_age)
    if first.captured_at>=second.captured_at or first.signature()!=second.signature():
        raise ValueError('地图视图正在变化，等待重新核对')
    pixel=second.world_to_client(target)
    if not _numbers(pixel,2):
        raise ValueError('地图投影无效')
    x,y=pixel;l,t,r,b=second.map_rect
    if not l+margin<x<r-margin or not t+margin<y<b-margin:
        raise ValueError('目标不在已确认的地图区域内，需要重新定位视图')
    if any(l-margin<=x<=r+margin and t-margin<=y<=b+margin
           for l,t,r,b in second.blocked_rects):
        raise ValueError('目标位置被地图控件遮挡')
    return MapClickPlan(tuple(target),pixel,second.scene,second.signature(),now)


def plan_map_pan(target,view,now,*,max_age,margin):
    """A bounded screen-space drag towards the target; not a guessed new aim.

    Both endpoints stay inside the current ROI. The .45 span is an engineering
    drag bound, not a world/map capability. The caller must read back real pan.
    """
    view.validate(now,max_age)
    if not _numbers((margin,),1) or margin<0:
        raise ValueError('地图拖动边距无效')
    l,t,r,b=view.map_rect;w,h=r-l-2*margin,b-t-2*margin
    if min(w,h)<=4:
        raise ValueError('地图拖动区域过小')
    center=((l+r)/2,(t+b)/2);pixel=view.world_to_client(target)
    dx=max(-w*.45,min(w*.45,center[0]-pixel[0]))
    dy=max(-h*.45,min(h*.45,center[1]-pixel[1]))
    if math.hypot(dx,dy)<1.:
        raise ValueError('当前目标无需平移或被控件遮挡')
    start=(center[0]-dx/2,center[1]-dy/2);end=(center[0]+dx/2,center[1]+dy/2)
    # Conservative bounding-box rejection also covers crossing a control.
    x1,x2=sorted((start[0],end[0]));y1,y2=sorted((start[1],end[1]))
    if any(x1<=br+margin and x2>=bl-margin and y1<=bb+margin and y2>=bt-margin
           for bl,bt,br,bb in view.blocked_rects):
        raise ValueError('地图拖动路径被控件遮挡')
    return start,end


def confirm_map_target(plan, first, second, now, *, requested_at, world_tolerance):
    """Validate native readback after a UI request, preserving saved XYZ."""
    if not _numbers((requested_at,world_tolerance,now),3) or world_tolerance<=0 \
            or not plan.prepared_at<=requested_at<=now:
        raise ValueError('地图设标请求时间或容差无效')
    a,b=validate_guidance(first,now),validate_guidance(second,now)
    if not requested_at<=a['captured_at']<b['captured_at']<=now:
        raise ValueError('尚未取得本次设标后的两份引导数据')
    for value in (a,b):
        if value.get('scene_context')!=plan.scene or not value.get('tracking_id') \
                or math.hypot(value['target'][0]-plan.target[0],value['target'][2]-plan.target[2])>world_tolerance:
            raise ValueError('游戏读回目标与本次采集点不符')
    if a['tracking_id']!=b['tracking_id'] or a['revision']!=b['revision']:
        raise ValueError('游戏设标目标或路径尚未稳定')
    return {'tracking_id':b['tracking_id'],'saved_target':plan.target,'route':b,
            'path_end_gap':math.hypot(b['path'][-1][0]-plan.target[0],b['path'][-1][2]-plan.target[2]),
            'collection_complete':False}
