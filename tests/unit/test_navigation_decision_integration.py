"""Offline cross-module regression. No game process, F6 or real input."""
import json
import math
from copy import deepcopy
from tests.unit.test_coordinate_player import Game, manual_points
from tests.unit.test_coordinate_navigation import scene
from utils.core.coordinate_navigation import LocalNavigator
from utils.core.direction_evidence import DirectionEvidence
from utils.drivers.route_path_memory import RoutePathMemory
from utils.flows.coordinate_player import drive_points, _Follower, _CalibrationRetry


def test_nonfinite_direction_observation_is_unknown_and_can_be_logged():
    result = DirectionEvidence().observe((0,1),(math.nan,1),now=1.)
    assert result['state'] == 'unknown'
    json.dumps(result,allow_nan=False)


def test_memory_reuse_cannot_bypass_new_directed_risk_or_modify_saved_route(tmp_path):
    data = manual_points([(0,0,8)])
    memory = RoutePathMemory(tmp_path/'route.json',data)
    assert memory.remember(0,[[0.,0.,float(z)] for z in range(9)])
    before = memory.path.read_bytes()
    nav = LocalNavigator(lambda:scene(),water_areas=(),path_memory=memory)
    nav.set_waypoint(0)
    nav.next_target((0,0,0),(0,0,8))
    nav.blocked((0,0,0),(0,0,3))
    selected = nav.next_target((0,0,0),(0,0,8))
    assert abs(selected[0]) > .1
    assert memory.path.read_bytes() == before


def test_skipped_final_point_and_multiple_cycles_never_report_success(monkeypatch):
    game, calls, reports = Game(), [], []
    def failed(self,reference,target_index=0):
        calls.append(game.time)
        raise _CalibrationRetry('synthetic uncertain direction')
    monkeypatch.setattr(_Follower,'calibrate',failed)
    data = manual_points([(0,0,5)]);before = deepcopy(data)
    nav = LocalNavigator(lambda:scene(),clock=game.monotonic,water_areas=())
    assert not drive_points(data,game.size(),lambda:game,game,game,game,
                            lambda state,message,**extra:reports.append((state,extra)),clock=game,
                            navigator=nav,loop_count=2)
    assert len(calls) == 6
    assert reports[-1][0] == 'partial' and reports[-1][1]['skipped_count'] == 2
    assert not any(state in ('reached','cycle_finished') for state,_ in reports)
    assert data == before and not game.keys and game.closed


def test_stop_during_direction_probe_releases_all_input_without_skipping():
    game, reports = Game(), []
    game.stop_at = .15
    nav = LocalNavigator(lambda:scene(),clock=game.monotonic,water_areas=())
    assert not drive_points(manual_points([(0,0,5)]),game.size(),lambda:game,game,game,game,
                            lambda state,message,**extra:reports.append(state),clock=game,navigator=nav)
    assert 'skipped' not in reports and not game.keys and game.closed


def test_final_short_connector_is_scored_instead_of_bypassing_entry_risk():
    from utils.core.approach_risk import ApproachRisks
    from utils.core.coordinate_navigation import ObstacleMap, plan_path
    risks = ApproachRisks(clock=lambda:0.)
    risks.record((0,0,0),(0,0,4))
    obstacles = ObstacleMap(scene(),(0,0,0),water_areas=(),risks=risks)
    path = plan_path((0,0,0),(-.5,0,.6),obstacles)
    projected = [(0,0)]+[(p[0],p[2]) for p in path]
    cost = sum(obstacles.travel_cost(a,b) for a,b in zip(projected,projected[1:]))
    candidate = [(0,0),(-1.5,0),(-1.5,.6),(-.5,.6)]
    reference = sum(obstacles.travel_cost(a,b) for a,b in zip(candidate,candidate[1:]))
    assert cost <= reference+1e-8


def test_geometry_or_scene_refresh_cannot_reset_total_calibration_budget_forever():
    from utils.core.direction_evidence import CalibrationBudget
    budget = CalibrationBudget()
    for i in range(6):
        assert budget.begin((0,0,0),('cycle',1),f'scene-{i}')
    assert not budget.begin((0,0,0),('cycle',1),'another-refresh')
