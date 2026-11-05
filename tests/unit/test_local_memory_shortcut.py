from unittest.mock import Mock
from copy import deepcopy
import pytest
from tests.unit.test_memory_quality import navigator, ready_scene
from tests.unit.test_coordinate_navigation import box
from utils.core.coordinate_navigation import ObstacleMap


PATH=[(-3,0,0),(-3,0,3),(0,0,3),(0,0,6),(0,0,9)]


def prepared(tmp_path,source=None):
    nav,memory=navigator(tmp_path,source or ready_scene())
    nav.scene=source or ready_scene();nav.obstacles=ObstacleMap(nav.scene,(0,0,0),water_areas=())
    nav.memory_path=list(PATH)
    return nav,memory


def test_local_rejoin_does_not_need_a_direct_connection_to_the_final_destination(tmp_path):
    nav,memory=prepared(tmp_path);before=deepcopy(memory.legs)
    assert nav._memory_target((0,0,0))==(0,0,9)
    assert nav.memory_path==PATH and memory.legs==before
    assert nav.local_shortcut.diagnostics['state']=='trial'


@pytest.mark.parametrize('fault',['wall','stale','height','coverage'])
def test_invalid_local_rejoin_is_explained_and_never_authorized(tmp_path,fault):
    nav,_=prepared(tmp_path,ready_scene(box(-1,1,1,2)) if fault=='wall' else None)
    if fault=='stale':nav.scene_clock=lambda:100.
    if fault=='height':nav.memory_path=[(x,1,z) for x,_,z in PATH]
    if fault=='coverage':nav.obstacles.covers_search=Mock(return_value=False)
    nav._memory_target((0,0,0))
    assert nav.local_shortcut.aim is None
    assert nav.local_shortcut.diagnostics['state']=='rejected'


def test_new_obstacle_revokes_a_local_trial_and_cools_down_its_rejoin(tmp_path):
    nav,_=prepared(tmp_path);assert nav._memory_target((0,0,0))==(0,0,9)
    nav.scene=ready_scene(box(-1,1,1,2));nav.obstacles=ObstacleMap(nav.scene,(0,0,0),water_areas=())
    nav._memory_target((0,0,0));assert nav.local_shortcut.aim is None
    assert nav.local_shortcut.rejected


def test_local_trial_does_not_use_old_aim_in_another_scene(tmp_path):
    nav,_=prepared(tmp_path);nav._memory_target((0,0,0))
    nav.scene['cache_context']='other';nav.scene_clock=lambda:100.
    nav._memory_target((0,0,0));assert nav.local_shortcut.aim is None


def test_controller_rejoins_a_local_point_and_logs_the_trial(tmp_path):
    nav,_=prepared(tmp_path);target=(0,1,30)
    nav.memory_loaded=True;nav.memory_scope='world'
    nav.memory_leg=(0,target)
    # Use the actual scope identity, not a display label.
    from utils.core.traversal_evidence import scene_scope
    nav.memory_scope=scene_scope(nav.scene)
    before=list(nav.memory_path)
    aim=nav.next_target((0,0,0),target)
    assert aim[0]==0 and aim[2]>0 and nav.memory_aim==(0,0,9)
    assert nav.memory_path==before
    assert nav.last_decision['memory_shortcut']['state']=='trial'


def test_stopped_validation_propagates_instead_of_continuing_a_shortcut(tmp_path):
    nav,_=prepared(tmp_path)
    nav._shortcut_guard=Mock(side_effect=InterruptedError('stopped'))
    with pytest.raises(InterruptedError):nav._memory_target((0,0,0))
    assert nav.local_shortcut.aim is None


def test_failed_local_trial_does_not_penalize_the_old_corridor(tmp_path):
    nav,memory=prepared(tmp_path);nav._memory_target((0,0,0))
    memory.selected=memory._identity(memory.legs['0'][0])
    nav.blocked((0,0,0),(0,0,9),attempt_id=1)
    assert not memory.rejected and nav.local_shortcut.rejected
