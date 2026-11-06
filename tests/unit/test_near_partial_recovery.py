from types import SimpleNamespace
from unittest.mock import Mock

from tests.unit.test_collection_continuity import game_and_nav
from utils.core.coordinate_recovery import RecoveryProgress
from utils.flows.coordinate_player import _Follower


def test_partial_memory_aim_inside_reach_radius_does_not_stall_saved_goal():
    game,nav=game_and_nav()
    nav.traversal_memory=SimpleNamespace(candidates=lambda *args:[{
        'start':(0.,0.,0.),'end':(.25,0.,0.),'failures':0}])
    aim=nav.next_target((0.,0.,0.),(.74,0.,0.))
    assert aim==(.74,0.,0.)
    assert nav.last_decision.get('kind')!='observed_partial_memory'


def test_episode_origin_at_local_target_returns_failure_without_global_abort():
    follower=_Follower.__new__(_Follower)
    follower.release=Mock();follower.read=Mock(return_value=(-.5,0.,0.))
    follower.recovery=RecoveryProgress(origin=(0.,0.,0.))
    follower.travel_progress=Mock();follower.report=Mock()
    follower.waypoints=[{}];follower.navigator=object();follower.recovery_attempts=5
    follower.recovery_leg=Mock(side_effect=AssertionError('No undefined recovery direction'))
    assert follower.recover((.25,0.,0.),None,(-.5,0.,0.),0,1) is False
    assert follower.recovery.pending and follower.recovery.attempts==1
    assert follower.report.call_args.kwargs['recovery_result']=='degenerate_local_target'
