"""Deleting one point preserves other data and never overwrites a stale route."""
import json
from copy import deepcopy
import pytest
from utils.core.coordinate_points import append_point, empty_points, remove_point
from utils.drivers.coordinate_point_store import PointStore
from tests.unit.test_coordinate_points import SNAPSHOT, STAMP


def route():
 data=empty_points()
 for i in range(3):
  data=append_point(data,{**SNAPSHOT,'position':[i,20,i]},STAMP,collect_wait=i+1)
 data['points'][2]['name']='自定义橘子树'
 return data


def test_remove_middle_preserves_positions_names_metadata_and_order():
 original=route();before=deepcopy(original)
 result=remove_point(original,2)
 assert original==before
 assert [p['id'] for p in result['points']]==[1,2]
 assert [p['position'][0] for p in result['points']]==[0,2]
 assert result['points'][1]['name']=='自定义橘子树'
 assert result['points'][1]['collect_wait']==3


@pytest.mark.parametrize('point_id',[0,4,-1,True,None])
def test_bad_id_cannot_remove_another_point(point_id):
 with pytest.raises(ValueError):remove_point(route(),point_id)


def test_store_deletion_backs_up_exact_old_file_and_keeps_other_routes(tmp_path):
 store=PointStore(tmp_path/'苹果.json');store.create(route())
 other=PointStore(tmp_path/'蓝莓.json');other.create(route());raw_other=other.path.read_bytes()
 raw=store.path.read_bytes();backup=tmp_path/'路线回收站/苹果-删点前.json'
 result,path=store.delete_point(2,backup,expected_data=store.load())
 assert path==backup and backup.read_bytes()==raw
 assert store.load()==result and other.path.read_bytes()==raw_other
 assert len(result['points'])==2


def test_stale_editor_cannot_delete_after_another_window_saves(tmp_path):
 store=PointStore(tmp_path/'苹果.json');store.create(route());old=store.load()
 store.set_collect_wait(2,10);raw=store.path.read_bytes();backup=tmp_path/'backup.json'
 with pytest.raises(ValueError):store.delete_point(2,backup,expected_data=old)
 assert store.path.read_bytes()==raw and not backup.exists()


def test_last_point_can_be_removed_and_route_remains_valid(tmp_path):
 store=PointStore(tmp_path/'苹果.json');store.create(append_point(empty_points(),SNAPSHOT,STAMP))
 result,_=store.delete_point(1,tmp_path/'backup.json')
 assert result['points']==[] and store.load()['points']==[]
 assert store.append(SNAPSHOT,STAMP)['points'][0]['id']==1


def test_backup_failure_preserves_original(tmp_path):
 store=PointStore(tmp_path/'苹果.json');store.create(route());raw=store.path.read_bytes()
 backup=tmp_path/'backup.json';backup.write_text('existing')
 with pytest.raises(OSError):store.delete_point(2,backup)
 assert store.path.read_bytes()==raw

@pytest.mark.parametrize('confirm',[True,False])
def test_editor_deletes_only_confirmed_selected_point_and_updates_parent(tmp_path,monkeypatch,confirm):
 from unittest.mock import Mock
 from utils.ui.point_wait_editor import PointWaitEditor,messagebox
 store=PointStore(tmp_path/'苹果.json');store.create(route());old=store.load()
 editor=PointWaitEditor.__new__(PointWaitEditor)
 editor.store=store;editor.data=old;editor.root=Mock();editor.notice=Mock()
 editor.points=Mock();editor.points.curselection.return_value=(1,)
 editor.on_saved=Mock();editor.refresh=Mock()
 monkeypatch.setattr(messagebox,'askyesno',Mock(return_value=confirm))
 monkeypatch.setattr(messagebox,'showerror',Mock())
 editor.delete_selected()
 assert len(store.load()['points'])==(2 if confirm else 3)
 assert editor.on_saved.call_count==int(confirm)
 if confirm:assert len(list((tmp_path/'路线回收站').glob('*.json')))==1
 else:assert store.load()==old
 messagebox.showerror.assert_not_called()


def test_stale_wait_editor_cannot_change_renumbered_point(tmp_path):
 store=PointStore(tmp_path/'苹果.json');store.create(route());old=store.load()
 store.delete_point(1,tmp_path/'backup.json')
 current=store.load()
 with pytest.raises(ValueError):store.set_collect_wait(2,99,expected_data=old)
 assert store.load()==current
 assert current['points'][0]['id']==1 and current['points'][0]['name']=='点位 1'


def test_failed_atomic_save_preserves_route_and_original_backup(tmp_path,monkeypatch):
 store=PointStore(tmp_path/'苹果.json');store.create(route());raw=store.path.read_bytes()
 backup=tmp_path/'backup.json'
 def fail(data):raise OSError('disk failure')
 monkeypatch.setattr(store,'_write',fail)
 with pytest.raises(OSError):store.delete_point(2,backup)
 assert store.path.read_bytes()==raw and backup.read_bytes()==raw
