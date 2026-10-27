"""Zero-thickness vertical collision planes: reader, planning and display."""
import math
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.unit.test_guidance_planar_scene import plane_reader
from tests.unit.test_coordinate_navigation import scene, assert_clear
from tests.unit.test_scene_display_cache import scene as display_scene
from tests.unit.test_map_draw_cache import Canvas
from utils.core.scene_map import box_geometry
from utils.core.coordinate_navigation import ObstacleMap, LocalNavigator, plan_path, scene_geometry_signature
from utils.core.scene_display_cache import SceneDisplayCache
from utils.core.bridge_navigation import detect_bridge_decks
from utils.ui.map_draw_cache import MapDrawCache, Viewport
from utils.ui.scene_map_worker import SceneMapWorker


def segment_wall(y=0.,z=2.):
    return {'polygon':[[-2.,z],[2.,z]],'height':[y,y+2.],
            'surface_kind':'zero-z-box-plane','projection_kind':'segment'}


def test_actual_one_by_two_by_zero_managed_shape_can_publish_navigation():
    reader=plane_reader((1.,2.,0.))
    value=reader.snapshot()
    assert value['quality']['managed_unknown']==0
    assert len(value['colliders'])==1
    assert value['colliders'][0]['projection_kind']=='segment'
    worker=SceneMapWorker('profile',factory=lambda *a:reader)
    worker.set_pid(7);worker.poll_once()
    assert worker.navigation_snapshot(7) is not None
    worker.close()


@pytest.mark.parametrize('size',[(1,2,0),(0,2,1),(4,0,6)])
def test_planar_axes_preserve_real_corners_and_default_box_stays_strict(size):
    plane=box_geometry((0,0,0),(0,0,0,1),(0,1,0),size,allow_planar=True)
    assert len(plane['display_box']['corners'])==8
    assert plane['surface_kind']==f'zero-{"xyz"[size.index(0)]}-box-plane'
    with pytest.raises(ValueError):box_geometry((0,0,0),(0,0,0,1),(0,1,0),size)


@pytest.mark.parametrize('angle',[2.,178.,90.,-47.])
def test_parent_y_rotation_scale_and_q_sign_keep_exact_vertical_plane(angle):
    q=(0,math.sin(math.radians(angle)/2),0,math.cos(math.radians(angle)/2))
    plane=box_geometry((0,0,0),(0,0,0,-1),(0,1,0),(1,2,0),((10,20,30),q,2),allow_planar=True)
    assert plane['projection_kind']=='segment'
    assert len(plane['polygon'])==2
    assert plane['height']==[20.,24.]
    assert math.dist(*plane['polygon'])==pytest.approx(2.)
    assert all(20<=p[1]<=24 for p in plane['display_box']['corners'])


def test_exact_rotated_zero_y_plane_becomes_vertical_without_artificial_thickness():
    plane=box_geometry((0,0,0),(.5,.5,.5,.5),(0,0,0),(6,0,8),allow_planar=True)
    assert plane['projection_kind']=='segment' and len(plane['polygon'])==2


def test_genuinely_tilted_plane_keeps_area_instead_of_becoming_an_exact_wall_line():
    angle=math.pi/2-1e-4
    plane=box_geometry((0,0,0),(math.sin(angle/2),0,0,math.cos(angle/2)),(0,0,0),
                       (6,0,8),allow_planar=True)
    assert 'projection_kind' not in plane
    assert len(plane['polygon'])>=3


@pytest.mark.parametrize('size',[(0,0,1),(0,0,0),(-1,2,0),(1,math.nan,0),(math.inf,2,0)])
def test_invalid_degenerate_or_nonfinite_shapes_remain_unknown(size):
    value=plane_reader(size).snapshot()
    assert not value['quality']['navigation_eligible']
    with pytest.raises(ValueError):box_geometry((0,0,0),(0,0,0,1),(0,0,0),size,allow_planar=True)


def test_crossing_is_blocked_both_directions_and_clearance_is_kept():
    obstacles=ObstacleMap(scene(segment_wall()),(0,0,0),water_areas=())
    assert not obstacles.clear((0,0),(0,4))
    assert not obstacles.clear((0,4),(0,0))
    assert not obstacles.clear((-1,1.9),(1,1.9))
    assert obstacles.clear((-1,0),(1,0))


def test_ground_overhead_and_other_layer_remain_separate():
    assert ObstacleMap(scene(segment_wall(8)),(0,0,0),water_areas=()).clear((0,0),(0,4))
    assert not ObstacleMap(scene(segment_wall(8)),(0,8,0),water_areas=()).clear((0,0),(0,4))
    assert ObstacleMap(scene(segment_wall(-3)),(0,0,0),water_areas=()).clear((0,0),(0,4))


def test_astar_and_all_simplified_chords_go_around_the_segment():
    obstacles=ObstacleMap(scene(segment_wall()),(0,0,0),water_areas=())
    path=plan_path((0,0,0),(0,0,6),obstacles)
    assert max(abs(p[0]) for p in path)>2.5
    assert_clear(path,obstacles)


def test_signature_and_old_path_reuse_cannot_ignore_new_segment():
    initial=scene();fresh=scene(segment_wall())
    assert scene_geometry_signature(initial)!=scene_geometry_signature(fresh)
    provider=[initial]
    navigator=LocalNavigator(lambda:provider[0],water_areas=())
    assert navigator.next_target((0,0,0),(0,0,6))[0]==0
    provider[0]=fresh
    next_point=navigator.next_target((0,0,0),(0,0,6))
    assert next_point[0]!=0
    assert not navigator.obstacles.clear((0,0),(0,6))


def test_quantized_steering_does_not_enter_the_segment():
    nav=LocalNavigator(lambda:scene(segment_wall()),water_areas=())
    nav.obstacles=ObstacleMap(scene(segment_wall()),(0,0,1.9),water_areas=())
    keys=nav.steering_keys((0,0,1.9),(0,0,4),{'forward':[0.,1.],'right':[1.,0.]},.3)
    assert keys and 'w' not in keys


def test_a_segment_cannot_be_mistaken_for_a_bridge_deck():
    source=scene(dict(segment_wall(),source='verified-solid-collider'))
    source['quality']={'navigation_eligible':True}
    assert detect_bridge_decks(source,(0,0,0),([[-3,1],[3,1],[3,3],[-3,3]],))==()


def test_unmarked_two_point_polygon_does_not_become_valid_geometry():
    unmarked=segment_wall();unmarked.pop('projection_kind')
    assert scene_geometry_signature(scene(unmarked))==scene_geometry_signature(scene())


def test_fixed_display_history_retains_segment_but_world_change_removes_it():
    cache=SceneDisplayCache()
    wall=dict(segment_wall(),map_static=True,map_id=('wall',1))
    cache.update(display_scene(boxes=[wall]),0.)
    assert len(cache.update(display_scene(x=200),1.)['colliders'])==1
    assert not cache.update(display_scene(x=200,context=('world',2)),2.)['colliders']


@pytest.mark.parametrize('method',['polygon','barrier','collision_detail'])
def test_display_draws_vertical_segment_as_a_line_without_fabricating_top(method):
    canvas=Canvas();canvas.create_polygon=Mock(side_effect=AssertionError('vertical segment must be a line'))
    canvas.create_line=Mock(wraps=canvas.create_line)
    cache=MapDrawCache(canvas);cache.begin(Viewport(400,400,0,0,10))
    wall=segment_wall();before=deepcopy(wall)
    if method=='polygon':cache.polygon(('test',1),(-2,2,2,2),0,fill='',outline='#858585',width=1)
    elif method=='barrier':cache.barrier(('test',1),(-2,2,2,2),'#858585','fence')
    else:cache.collision_detail(('test',1),wall)
    canvas.create_line.assert_called()
    assert wall==before
