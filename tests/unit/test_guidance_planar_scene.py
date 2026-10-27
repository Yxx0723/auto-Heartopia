"""Regression for a real scene blocked by zero-Y managed plane boxes."""
import math
import struct
import time
from unittest.mock import Mock

import pytest

from tests.unit.test_scene_read_recovery import minimal_scene_reader
from tests.unit.test_coordinate_navigation import scene
from utils.core.scene_map import box_geometry
from utils.core.scene_read import navigation_quality_valid
from utils.core.coordinate_navigation import ObstacleMap
from utils.ui.scene_map_worker import SceneMapWorker


def plane_reader(size=(6., 0., 8.)):
    reader = minimal_scene_reader()
    m = reader.meta
    reader.read_registry = Mock(return_value=[2])
    reader.barrier_owners = Mock(return_value={})
    reader.collider_barrier = Mock(return_value=None)
    m.obj.side_effect = lambda at: ({'name':'PhysicGroup','address':22} if at == 22 else
        {'name':'DynamicBoxCollider','address':300} if at == 3000 else
        {'name':'Hierarchy','namespace':'XDTGame.Core','address':99})
    refs = {(2,'_hierarchyObject'):22, (22,'_colliders'):1000,
            (1001,'_stream'):2000, (2000,'_collisions'):2001, (2002,'colliders'):2003,
            (3000,'_group'):22, (3000,'_hierarchy'):2, (2,'_parent'):0}
    m.ref.side_effect = lambda obj,name: refs[obj,name]
    m.dictionary.side_effect = lambda obj: {1000:[1001],2001:[2002]}.get(obj,[])
    m.array_refs.return_value = [3000]
    fields = {'_enabled':16,'_colliderData':32,'IsTrigger':16,'Position':20,'Rotation':32,
              'Center':48,'Size':60,'_position':16,'_rotation':28,'__scale':44}
    m.field.side_effect = lambda cls,name: {'offset':fields[name],'type':400}
    m.cls.return_value = {'address':400}
    start = 3016
    memory = {(3016,1):b'\x01',(start+16,1):b'\0',
              (start+20,12):struct.pack('<3f',0,0,0),(start+32,16):struct.pack('<4f',0,0,0,1),
              (start+48,12):struct.pack('<3f',0,0,0),(start+60,12):struct.pack('<3f',*size),
              (18,12):struct.pack('<3f',0,0,0),(30,16):struct.pack('<4f',0,0,0,1),(46,4):struct.pack('<f',1)}
    m.data.side_effect = lambda at,n: memory[at,n]
    return reader


def test_real_managed_plane_is_not_an_unknown_collision_and_worker_can_publish_it():
    reader = plane_reader()
    snapshot = reader.snapshot()
    assert snapshot['quality']['managed_unknown'] == 0
    assert snapshot['quality']['parts']['managed_collisions']['state'] == 'complete'
    assert navigation_quality_valid(snapshot, time.monotonic())
    assert len(snapshot['colliders']) == 1
    assert snapshot['colliders'][0]['height'] == [0., 0.]
    worker = SceneMapWorker('profile', factory=lambda *args: reader)
    worker.set_pid(7);worker.poll_once()
    assert worker.navigation_snapshot(7) is not None
    worker.close()


def test_plane_keeps_exact_zero_height_corners_and_actual_projected_footprint():
    plane = box_geometry((1,3,2), (0,0,0,1), (0,0,0), (6,0,8), allow_planar=True)
    assert plane['height'] == [3.,3.]
    assert {p[1] for p in plane['display_box']['corners']} == {3.}
    assert set(map(tuple,plane['polygon'])) == {(-2.,-2.),(4.,-2.),(4.,6.),(-2.,6.)}
    assert plane['surface_kind'] == 'zero-y-box-plane'


def test_floor_and_overhead_planes_do_not_block_but_body_intersecting_plane_does():
    def plane(y):return box_geometry((0,y,0),(0,0,0,1),(0,0,0),(6,0,8),allow_planar=True)
    assert ObstacleMap(scene(plane(0),plane(3)),(0,0,0),water_areas=()).clear((0,0),(0,3))
    assert not ObstacleMap(scene(plane(1)),(0,0,0),water_areas=()).clear((0,0),(0,3))


def test_rotated_scaled_plane_retains_transform_and_height_without_filling_a_volume():
    parent = ((10,2,20),(0,math.sqrt(.5),0,math.sqrt(.5)),2.)
    plane = box_geometry((1,0,0),(0,0,0,1),(0,0,0),(2,0,4),parent,allow_planar=True)
    assert plane['height'] == [2.,2.]
    xs,zs=zip(*plane['polygon'])
    assert (min(xs),max(xs),min(zs),max(zs)) == pytest.approx((6,14,16,20))


@pytest.mark.parametrize('size', [(-1,0,8),(6,-1,8),(0,0,8),(0,2,8),(6,math.nan,8)])
def test_other_invalid_shapes_stay_unknown_and_do_not_loosen_gate(size):
    with pytest.raises(ValueError):box_geometry((0,0,0),(0,0,0,1),(0,0,0),size,allow_planar=True)
    snapshot = plane_reader(size).snapshot()
    assert not snapshot['quality']['navigation_eligible']


def test_default_strict_box_api_still_rejects_planes():
    with pytest.raises(ValueError):box_geometry((0,0,0),(0,0,0,1),(0,0,0),(6,0,8))


def test_vertical_plane_with_only_a_line_projection_remains_unknown():
    # Exact axis-permuting rotation: a vertical plane needs dedicated narrow
    # phase support, so never discard it and publish a complete scene.
    with pytest.raises(ValueError, match='XZ footprint'):
        box_geometry((0,0,0),(.5,.5,.5,.5),(0,0,0),(6,0,8),allow_planar=True)


def test_opt_in_does_not_change_a_real_solid_wall_or_its_collision_constraint():
    args=((0,1,0),(0,0,0,1),(0,0,0),(1,2,8))
    wall=box_geometry(*args,allow_planar=True)
    assert wall == box_geometry(*args)
    assert not ObstacleMap(scene(wall),(0,0,0),water_areas=()).clear((-2,0),(2,0))
