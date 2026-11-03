"""Pure UI transform math. Rectangles are canvas-local, not game ground XYZ."""
import math


def _finite(values,count):
    return isinstance(values,(tuple,list)) and len(values)==count and all(
        type(x) in (int,float) and math.isfinite(x) for x in values)


def _rotate(p,q):
    x,y,z,w=q;px,py,pz=p
    tx,ty,tz=2*(y*pz-z*py),2*(z*px-x*pz),2*(x*py-y*px)
    return (px+w*tx+y*tz-z*ty,py+w*ty+z*tx-x*tz,pz+w*tz+x*ty-y*tx)


def transform_point(point,chain,*,inverse=False):
    if not _finite(point,3) or not isinstance(chain,(tuple,list)) or not 1<=len(chain)<=64:
        raise ValueError('界面变换链无效')
    point=tuple(point)
    for record in reversed(chain) if inverse else chain:
        if not isinstance(record,(tuple,list)) or len(record)!=3:
            raise ValueError('界面变换记录无效')
        pos,rot,scale=record
        if not _finite(pos,3) or not _finite(rot,4) or not _finite(scale,3) \
                or any(abs(s)<1e-8 for s in scale) \
                or not math.isclose(sum(x*x for x in rot),1.,abs_tol=1e-4):
            raise ValueError('界面变换数值无效')
        if inverse:
            point=_rotate(tuple(x-y for x,y in zip(point,pos)),(-rot[0],-rot[1],-rot[2],rot[3]))
            point=tuple(x/y for x,y in zip(point,scale))
        else:
            point=_rotate(tuple(x*y for x,y in zip(point,scale)),rot)
            point=tuple(x+y for x,y in zip(point,pos))
    if not _finite(point,3):
        raise ValueError('界面变换结果无效')
    return point


def rect_to_client(rect,chain,canvas_rect,canvas_chain,client_size):
    if not _finite(rect,4) or not _finite(canvas_rect,4) or min(rect[2:])<=0 \
            or min(canvas_rect[2:])<=0 or not _finite(client_size,2) or min(client_size)<=0:
        raise ValueError('界面矩形参数无效')
    x,y,w,h=rect;cx,cy,cw,ch=canvas_rect;sw,sh=client_size
    corners=[]
    for a,b in ((x,y),(x+w,y),(x+w,y+h),(x,y+h)):
        world=transform_point((a,b,0.),chain)
        local=transform_point(world,canvas_chain,inverse=True)
        corners.append(((local[0]-cx)*sw/cw,sh-(local[1]-cy)*sh/ch))
    for a,b in zip(corners,corners[1:]+corners[:1]):
        if not (math.isclose(a[0],b[0],abs_tol=1e-4) or math.isclose(a[1],b[1],abs_tol=1e-4)):
            raise ValueError('旋转界面矩形尚不支持自动点击')
    return (min(p[0] for p in corners),min(p[1] for p in corners),
            max(p[0] for p in corners),max(p[1] for p in corners))
