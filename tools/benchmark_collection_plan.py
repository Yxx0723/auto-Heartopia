"""Reproducible offline comparison against the pre-plan git revision; no game IO."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import types
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from utils.core import coordinate_navigation as current, thresholds as policy


def benchmark(output):
    baseline=types.ModuleType('baseline_navigation')
    source=subprocess.check_output(['git','show','2072b18:utils/core/coordinate_navigation.py'],cwd=Path(__file__).resolve().parents[1])
    exec(compile(source,'baseline_navigation','exec'),baseline.__dict__)
    def box(a,b,c,d):return {'polygon':[[a,c],[b,c],[b,d],[a,d]],'height':[-.5,2.]}
    world={'state':'ready','objects':[],'colliders':[box(-3,3,3,4),box(-3,-2,-6,4),box(2,3,-6,4)]}
    def run(module):
        now=[0.];nav=module.LocalNavigator(lambda:world,water_areas=(),clock=lambda:now[0])
        position=(0.,0.,0.);target=(0.,0.,30.);length=0.;partials=0;accepted=0;results={}
        original=module.plan_path
        def counted(position,target,obstacles,*args,**kwargs):
            nonlocal partials
            path=original(position,target,obstacles,*args,**kwargs)
            gap=math.hypot(target[0]-position[0],target[2]-position[2]);h=kwargs['horizon']
            local=target if gap<=h else (position[0]+(target[0]-position[0])*h/gap,0.,position[2]+(target[2]-position[2])*h/gap)
            results[id(path)]=math.hypot(path[-1][0]-local[0],path[-1][2]-local[2])>1e-6
            partials+=int(results[id(path)])
            return path
        module.plan_path=counted
        started=time.perf_counter()
        try:
            for step in range(250):
                if math.dist(position,target)<=.3:break
                # Simulate a changed snapshot invalidating the local corridor.
                if step%4==0:nav.invalidate()
                before=nav.stats['plans']
                try:aim=nav.next_target(position,target)
                except module.NavigationWait:
                    now[0]+=.1;continue
                if nav.stats['plans']!=before:accepted+=int(results.get(id(nav.path),False))
                gap=math.dist(position,aim);travel=min(.5,gap)
                if gap:position=tuple(a+(b-a)*travel/gap for a,b in zip(position,aim));length+=travel
                now[0]+=.1
            return {'arrived':math.dist(position,target)<=.3,'steps':step+1,'length':length,
                'plans':nav.stats['plans'],'partial_results':partials,'partial_accepts':accepted,'cpu_seconds':time.perf_counter()-started}
        finally:module.plan_path=original
    with patch.multiple(policy,COORD_NAV_SEARCH_RADIUS=4.,COORD_NAV_HORIZON=8.,COORD_NAV_FINE_SEARCH_RADIUS=8.,
            COORD_NAV_WIDE_SEARCH_RADIUS=14.,COORD_NAV_WIDE_HORIZON=12.,COORD_NAV_MIN_PROGRESS=.5):
        wall={'baseline':run(baseline),'candidate':run(current)}
    def straight(module):
        times=[]
        for repeat in range(7):
            nav=module.LocalNavigator(lambda:{'state':'ready','colliders':[]},water_areas=())
            started=time.perf_counter()
            for i in range(100):nav.next_target((0,0,0),(0,0,10))
            times.append(time.perf_counter()-started)
        return sorted(times)[len(times)//2]
    direct={'baseline':straight(baseline),'candidate':straight(current),'maximum_ratio':1.2}
    direct['ratio']=direct['candidate']/direct['baseline']
    report={'scenario':'scaled U wall; snapshot invalidation every 4 ticks; ideal steering, no game input',
        'baseline_revision':'2072b18','wall':wall,'straight_seconds':direct,
        'real_game_verified':False}
    Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);benchmark(p.parse_args().output)
