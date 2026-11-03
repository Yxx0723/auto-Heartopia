"""UI coordinates are transformed through the canvas, never guessed pixels."""
import math
import pytest
from utils.core.ui_projection import transform_point, rect_to_client

IDENTITY=((0.,0.,0.),(0.,0.,0.,1.),(1.,1.,1.))


def test_canvas_scale_translation_and_nested_parent_round_trip():
    canvas=(((683.,384.,0.),(0.,0.,0.,1.),(.5,.5,.5)),IDENTITY)
    child=(((100.,-50.,0.),(0.,0.,0.,1.),(2.,2.,2.)),*canvas)
    world=transform_point((5.,6.,0.),child)
    assert world==pytest.approx((738.,365.,0.))
    assert transform_point(world,child,inverse=True)==pytest.approx((5.,6.,0.))
    result=rect_to_client((-10.,-5.,20.,10.),child,(-1366.,-768.,2732.,1536.),canvas,(1366,768))
    assert result==pytest.approx((723.,404.,743.,414.))


def test_non_uniform_scaled_rotated_parent_has_exact_inverse():
    q=(0.,0.,math.sin(.4),math.cos(.4))
    chain=(((10.,20.,0.),q,(2.,3.,1.)),IDENTITY)
    value=(3.,4.,5.)
    assert transform_point(transform_point(value,chain),chain,inverse=True)==pytest.approx(value)


@pytest.mark.parametrize('bad',[
    ((0.,0.,0.),(0.,0.,0.,0.),(1.,1.,1.)),
    ((0.,0.,0.),(0.,0.,0.,1.),(0.,1.,1.)),
    ((float('nan'),0.,0.),(0.,0.,0.,1.),(1.,1.,1.))])
def test_invalid_transforms_do_not_return_a_click_location(bad):
    with pytest.raises(ValueError):transform_point((0.,0.,0.),(bad,))


def test_rotated_click_rect_is_not_silently_replaced_by_its_bounding_box():
    chain=(((0.,0.,0.),(0.,0.,math.sin(.2),math.cos(.2)),(1.,1.,1.)),)
    with pytest.raises(ValueError):rect_to_client((-1.,-1.,2.,2.),chain,(-10.,-10.,20.,20.),(IDENTITY,),(100,100))
