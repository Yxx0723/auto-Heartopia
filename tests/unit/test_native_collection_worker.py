"""Native collection uses its own navigation supply, keeping resource reads."""
from unittest.mock import Mock

import pytest

from tests.unit.test_point_playback import FakeWindow,WaitingControls,points
from utils.ui.point_playback import PointPlayback


@pytest.mark.parametrize('failed',[False,True])
def test_owned_native_supply_is_closed_on_success_or_failure(monkeypatch,failed):
    supply=Mock();supply.navigation_snapshot.return_value={'native':True}
    supply.navigation_metrics.return_value={'usable_ratio':1.}
    factory=Mock(return_value=supply)
    monkeypatch.setattr('utils.ui.point_playback.SceneMapWorker',factory)
    resource={'resources':True};seen=[]
    def runner(*args,**kwargs):
        nav=kwargs['navigator'];seen.append(nav)
        assert nav.native_map_collection is False
        assert nav.scene_provider()=={'native':True}
        assert nav.native_scene_provider()=={'native':True}
        args[6]('playing','checking supply')
        if failed:raise ValueError('test failure')
        return True
    worker=PointPlayback('profile.json',runner=runner,window_factory=FakeWindow,
        controls_factory=WaitingControls,device_factory=lambda w:Mock(),
        scene_provider=lambda pid:resource,guidance_scene_factory=Mock())
    worker.start(points(),77,auto_navigate=True,remember_paths=False)
    worker.thread.join(2)
    assert not worker.active and len(seen)==1
    supply.set_pid.assert_called_once_with(77);supply.start.assert_called_once()
    supply.close.assert_called_once()
    assert worker.take_latest()['state']==('error' if failed else 'finished')
    worker.close()
