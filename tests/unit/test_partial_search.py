from unittest.mock import Mock
from utils.core import coordinate_navigation as navigation
from tests.unit.test_coordinate_navigation import scene, box


def test_u_wall_distinguishes_frontier_from_reached_local():
    s=scene(box(-3,3,3,4),box(-3,-2,-6,4),box(2,3,-6,4))
    obstacles=navigation.ObstacleMap(s,(0,0,0),water_areas=(),search_radius=20)
    partial=navigation.plan_path((0,0,0),(0,0,30),obstacles,search_radius=4,horizon=8,max_nodes=3000)
    full=navigation.plan_path((0,0,0),(0,0,30),obstacles,search_radius=14,horizon=8,max_nodes=15000)
    assert not partial.reached_local and full.reached_local
    assert full[-1]==(0.,0,8.)
    assert min(p[2] for p in full)<-6


def test_repeated_partial_upgrades_without_changing_risk_revision(monkeypatch):
    nav=navigation.LocalNavigator(lambda:scene(),water_areas=(),clock=lambda:1.)
    calls=[]
    def plan(*args,**kwargs):
        calls.append(kwargs)
        return navigation.PlannedPath([(0.,0.,5.)],(0.,10.),len(calls)>=4)
    monkeypatch.setattr(navigation,'plan_path',plan)
    for _ in range(3):
        nav.invalidate()
        nav.next_target((0,0,0),(0,0,30))
    assert len(calls)==4 and nav.search_diagnostics['escalated']
    assert nav.risks.revision==0


def test_planning_timeout_does_not_create_movement_risk():
    nav=navigation.LocalNavigator(lambda:scene(),water_areas=())
    for i in range(5):nav.blocked((0,0,0),(0,0,8),attempt_id=i,evidence='progress_timeout')
    assert nav.risks.revision==0
    nav.blocked((0,0,0),(0,0,8),attempt_id=6,evidence='movement_stall')
    assert nav.risks.revision==1
