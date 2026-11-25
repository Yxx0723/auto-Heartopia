"""Shared safety decisions for live fishing and the diagnostic state machine."""
import math

from utils.core import thresholds as policy


class TensionControl:
    def __init__(self):
        self.safe_frames = 0
        self.unknown_since = None

    def evaluate(self, color, held, now):
        color = color if color in ('white','yellow','orange','red') else 'unknown'
        if color == 'unknown':
            if self.unknown_since is None:
                self.unknown_since = now
        else:
            self.unknown_since = None
        if self.unknown_since is not None and now-self.unknown_since >= policy.FISHING_UNKNOWN_TENSION_SECONDS:
            return 'timeout'
        # Orange/red require immediate slack. Unknown evidence releases before
        # waiting for recovery. Yellow holds an existing pull, but cannot restart it.
        self.safe_frames = self.safe_frames+1 if color == 'white' else 0
        if held and color in ('unknown','orange','red'):
            return 'release'
        if not held and self.safe_frames >= 2:
            return 'press'
        return None


class FishTarget:
    def __init__(self):
        self.current = None
        self.last_at = None
        self.velocity = (0.,0.)
        self.frames = 0
        self.travel = 0.
        self.uncertainty = 0.

    def predict(self, seconds):
        return tuple(value+speed*seconds for value,speed in
                     zip((self.current.cx,self.current.cy),self.velocity))

    def update(self, candidates, now, size, *, locked=False):
        sx,sy = size[0]/1382,size[1]/807
        if self.current is not None and now-self.last_at > policy.FISHING_LOCK_MAX_GAP:
            if locked:
                return None
            self.__init__()
        if not candidates:
            return None
        if self.current is None:
            if locked:
                return None
            center = (size[0]*.50,size[1]*.35)
            self.current = max(candidates,key=lambda p: p.confidence
                               - .002*math.hypot((p.cx-center[0])/sx,(p.cy-center[1])/sy))
        else:
            dt = now-self.last_at
            if dt <= 0:
                return None
            x,y = self.predict(dt)
            ranked = sorted(((math.hypot((p.cx-x)/sx,(p.cy-y)/sy)
                              + 14*abs(math.log(p.area/self.current.area))
                              + 8*(1.-p.confidence),i,p) for i,p in enumerate(candidates)))
            score,_,best = ranked[0]
            if score > policy.FISHING_LOCK_RADIUS or (len(ranked)>1 and ranked[1][0]-score < 4.):
                return None
            dx,dy = best.cx-self.current.cx,best.cy-self.current.cy
            self.uncertainty = score
            self.travel += math.hypot(dx/sx,dy/sy)
            self.velocity = tuple(.35*old+.65*delta/dt for old,delta in zip(self.velocity,(dx,dy)))
            self.current = best
        self.frames += 1
        self.last_at = now
        return self.current

    @property
    def stable(self):
        return self.frames >= 3 and self.travel >= 3. and self.uncertainty <= 24.


def confirmed_catch(before, after, fish_ids):
    """One positive verified fish delta in the same live backpack scope."""
    if not before or not after or before['scope'] != after['scope']:
        return 0
    a,b = before['counts'],after['counts']
    delta = {i:b.get(i,0)-a.get(i,0) for i in a.keys()|b.keys()}
    changed = [(i,v) for i,v in delta.items() if v]
    return 1 if len(changed)==1 and changed[0][0] in fish_ids and changed[0][1]==1 else 0
