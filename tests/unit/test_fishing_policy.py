from types import SimpleNamespace

import cv2
import numpy as np

from utils.core.fishing_policy import FishTarget, TensionControl, confirmed_catch
from utils.vision.fishing_line import FishingLine


def test_blank_and_resized_line():
    for scale in (.5,1.,1.5):
        frame = np.zeros((round(807*scale),round(1382*scale),3),dtype=np.uint8)
        reader = FishingLine()
        assert reader.read(frame)['dom']=='unknown'
        cv2.line(frame,(round(570*scale),round(160*scale)),(round(574*scale),round(310*scale)),
                 (0,0,255),max(1,round(2*scale)))
        assert reader.read(frame)['dom']=='red'


def test_tension_requires_two_safe_samples_and_releases_immediately_on_unknown():
    control = TensionControl()
    assert control.evaluate('red',True,0)=='release'
    assert control.evaluate('white',False,.1) is None
    assert control.evaluate('white',False,.2)=='press'
    assert control.evaluate('unknown',True,.3)=='release'
    assert control.evaluate('unknown',False,2.)=='timeout'


def test_target_uses_prediction_avoids_ambiguous_crossing_and_expires():
    def point(x):
        return SimpleNamespace(cx=x,cy=280.,confidence=.95,area=1000)
    tracker = FishTarget()
    for t,x in enumerate((700,705,710)):
        assert tracker.update([point(x)],t*.1,(1382,807)) is not None
    assert tracker.stable
    assert tracker.update([point(714),point(715)],.3,(1382,807),locked=True) is None
    assert tracker.update([point(730)],1.,(1382,807),locked=True) is None


def test_hook_done_does_not_count_as_catch_without_scoped_inventory_delta():
    before = {'scope':'role-a','counts':{1:1}}
    after = {'scope':'role-a','counts':{1:2}}
    assert confirmed_catch(before,after,{1})==1
    assert confirmed_catch(before,after,set())==0
    assert confirmed_catch(before,dict(after,scope='role-b'),{1})==0
    assert confirmed_catch(before,before,{1})==0
