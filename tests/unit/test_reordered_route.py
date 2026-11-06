import pytest
from tests.unit.test_coordinate_player import manual_points
from utils.drivers.coordinate_point_routes import PointRoutes
from utils.drivers.coordinate_point_store import PointStore
from utils.drivers.route_path_memory import RoutePathMemory
from utils.drivers.playback_history import PlaybackHistory
from utils.core.coordinate_points import validate_points


def source(tmp_path):
    routes=PointRoutes(tmp_path/'original.json')
    routes.create('original',manual_points([(i,0,0) for i in range(4)]))
    return routes


def test_reorder_preserves_source_bytes_and_has_checked_mapping(tmp_path):
    routes=source(tmp_path);before=routes.path('original').read_bytes()
    routes.create_reordered('original','optimized',[1,3,2,4],[2,3])
    data=routes.store('optimized').load()
    assert routes.path('original').read_bytes()==before
    assert data['reordered_route']['new_to_old']==[1,3,2,4]
    data['points'][1]['position'][0]=999
    with pytest.raises(ValueError):validate_points(data)


def test_anchors_and_stale_source_rejected(tmp_path):
    routes=source(tmp_path)
    with pytest.raises(ValueError):routes.create_reordered('original','bad',[2,1,3,4],[2,3])
    with pytest.raises(ValueError):routes.create_reordered('original','bad',[1,3,2,4],[2,3],expected_sha256='0'*64)
    data=routes.store('original').load();data['points'][2]['position']=data['points'][1]['position'][:]
    routes.create('duplicates',data)
    with pytest.raises(ValueError):routes.create_reordered('duplicates','bad',[1,3,2,4],[2,3])


def test_candidate_memory_never_opens_shared_database(tmp_path):
    routes=source(tmp_path)
    routes.create_reordered('original','optimized',[1,3,2,4],[2,3])
    data=routes.store('optimized').load();identity=data['reordered_route']['isolation_id']
    root=tmp_path/'history';root.mkdir()
    shared=root/'寻路记忆库.sqlite3';shared.write_bytes(b'do not open this shared database')
    before=shared.read_bytes()
    history=PlaybackHistory(root,data,'optimized',{})
    assert history.error is None
    assert identity in str(history.memory.path) and history.memory.path!=shared
    memory=RoutePathMemory(routes.path('optimized'),data)
    assert identity in str(memory.path) and memory.count==0
    history.close()
    assert shared.read_bytes()==before
    routes.create('copy',data)
    assert routes.store('copy').load()['reordered_route']['isolation_id']!=identity


def test_failed_publication_leaves_source_and_selection_intact(tmp_path,monkeypatch):
    routes=source(tmp_path);before=routes.path('original').read_bytes()
    original=PointStore._write
    def fail(self,data):
        if self.path.stem=='optimized':raise OSError('simulated interruption')
        return original(self,data)
    monkeypatch.setattr(PointStore,'_write',fail)
    with pytest.raises(OSError):routes.create_reordered('original','optimized',[1,3,2,4],[2,3])
    assert routes.path('original').read_bytes()==before
    assert not routes.path('optimized').exists()
    assert routes.selected_name()=='original'
