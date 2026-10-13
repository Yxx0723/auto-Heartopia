"""Named point routes persist independently and never overwrite another route."""
import json

import pytest

from utils.core.coordinate_point_routes import validate_route_name
from utils.drivers.coordinate_point_routes import PointRoutes
from utils.drivers.coordinate_point_store import PointStore

STAMP = '2026-10-12T17:00:00+08:00'
SNAPSHOT = {'position': [1., 2., 3.], 'sample_at': STAMP}


@pytest.mark.parametrize('name', ['', ' ', '.', '..', '../苹果', 'a/b', 'a\\b', 'a:b',
                                 'a*', 'a?', 'a"', 'a<', 'a>', 'a|', 'a\x00', '.设置',
                                 '苹果.', 'CON', 'nul.txt', 'COM1', 'lpt9', '果' * 81, None])
def test_invalid_route_names_cannot_be_used_as_files(name):
    with pytest.raises(ValueError):
        validate_route_name(name)


def test_unicode_route_name_is_trimmed():
    assert validate_route_name('  蓝莓与树莓  ') == '蓝莓与树莓'


def test_existing_legacy_file_is_listed_without_rewriting_or_migrating(tmp_path):
    path = tmp_path / '我的点位.json'
    PointStore(path).append(SNAPSHOT, STAMP)
    before = path.read_bytes()
    routes = PointRoutes(path)
    assert routes.names() == ['我的点位']
    assert routes.selected_name() == '我的点位'
    assert routes.store('我的点位').load()['points'][0]['position'] == [1., 2., 3.]
    assert path.read_bytes() == before


def test_empty_library_can_save_the_default_route_without_setup(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    assert routes.names() == ['我的点位']
    routes.store('我的点位').append(SNAPSHOT, STAMP)
    assert len(routes.store('我的点位').load()['points']) == 1


def test_named_routes_and_save_as_keep_points_and_wait_settings_independent(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    routes.store('苹果').append(SNAPSHOT, STAMP, collect_wait=4.5)
    routes.create('蓝莓', routes.store('苹果').load())
    routes.store('蓝莓').append({**SNAPSHOT, 'position': [5., 2., 3.]}, STAMP)
    routes.store('蓝莓').set_collect_wait(1, 8)
    assert len(routes.store('苹果').load()['points']) == 1
    assert routes.store('苹果').load()['points'][0]['collect_wait'] == 4.5
    assert len(routes.store('蓝莓').load()['points']) == 2
    routes.remember('蓝莓')
    reopened = PointRoutes(tmp_path / '我的点位.json')
    assert reopened.selected_name() == '蓝莓'
    assert set(reopened.names()) == {'苹果', '蓝莓'}


def test_duplicate_names_do_not_replace_a_route_even_with_different_case(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('Apple')
    store = routes.store('Apple')
    store.append(SNAPSHOT, STAMP)
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        routes.create('apple')
    assert store.path.read_bytes() == before


def test_rename_preserves_exact_bytes_and_stale_window_cannot_recreate_old_file(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    stale = routes.store('苹果')
    stale.append(SNAPSHOT, STAMP)
    before = stale.path.read_bytes()
    routes.rename('苹果', '橘子')
    assert routes.store('橘子').path.read_bytes() == before
    with pytest.raises(ValueError, match='移动|删除'):
        stale.append(SNAPSHOT, STAMP)
    assert not stale.path.exists()


def test_failed_rename_preserves_both_routes(tmp_path, monkeypatch):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    routes.create('蓝莓')
    with pytest.raises(ValueError):
        routes.rename('苹果', '蓝莓')
    with monkeypatch.context() as patch:
        patch.setattr('utils.drivers.coordinate_point_store.os.rename',
                      lambda *args: (_ for _ in ()).throw(PermissionError('busy')))
        with pytest.raises(PermissionError):
            routes.rename('苹果', '树莓')
    assert set(routes.names()) == {'苹果', '蓝莓'}


def test_delete_moves_file_to_recoverable_archive_and_falls_back_to_remaining_route(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    routes.create('蓝莓')
    routes.store('苹果').append(SNAPSHOT, STAMP)
    routes.remember('苹果')
    before = routes.store('苹果').path.read_bytes()
    archive = routes.archive('苹果')
    assert archive.parent == tmp_path / '路线回收站'
    assert archive.read_bytes() == before
    assert routes.names() == ['蓝莓'] and routes.selected_name() == '蓝莓'


def test_bad_selection_metadata_falls_back_without_touching_route_data(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('蓝莓')
    routes.selection_path.write_text('{bad', encoding='utf-8')
    assert routes.selected_name() == '蓝莓'
    routes.selection_path.write_text(json.dumps({'selected_route': '../outside'}), encoding='utf-8')
    assert routes.selected_name() == '蓝莓'


def test_corrupt_route_cannot_be_copied_or_renamed_and_stays_untouched(tmp_path):
    path = tmp_path / '苹果.json'
    path.write_text('{bad', encoding='utf-8')
    routes = PointRoutes(tmp_path / '我的点位.json')
    with pytest.raises(ValueError):
        routes.create('蓝莓', routes.store('苹果').load())
    with pytest.raises(ValueError):
        routes.rename('苹果', '橘子')
    assert path.read_text(encoding='utf-8') == '{bad'
    assert not (tmp_path / '蓝莓.json').exists()


def test_failed_new_route_write_does_not_touch_existing_route(tmp_path, monkeypatch):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    before = routes.store('苹果').path.read_bytes()
    monkeypatch.setattr('utils.drivers.coordinate_point_store.os.replace',
                        lambda *args: (_ for _ in ()).throw(PermissionError('denied')))
    with pytest.raises(PermissionError):
        routes.create('蓝莓')
    assert routes.store('苹果').path.read_bytes() == before
    assert not (tmp_path / '蓝莓.json').exists() and not list(tmp_path.glob('*.tmp'))


def test_clear_points_keeps_route_name_other_routes_and_exact_recoverable_backup(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    routes.store('苹果').append(SNAPSHOT, STAMP, collect_wait=4.5)
    routes.create('蓝莓', routes.store('苹果').load())
    routes.remember('苹果')
    before = routes.store('苹果').path.read_bytes()
    other = routes.store('蓝莓').path.read_bytes()
    data, backup = routes.clear_points('苹果')
    assert data['points'] == []
    assert routes.store('苹果').load() == data
    assert routes.selected_name() == '苹果' and set(routes.names()) == {'苹果', '蓝莓'}
    assert routes.store('蓝莓').path.read_bytes() == other
    assert backup.parent == tmp_path / '路线回收站' and '清空前' in backup.name
    assert backup.read_bytes() == before
    assert routes.store('苹果').append(SNAPSHOT, STAMP)['points'][0]['id'] == 1


def test_empty_route_clear_does_not_create_backup_or_rewrite_file(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    before = routes.store('苹果').path.read_bytes()
    data, backup = routes.clear_points('苹果')
    assert data['points'] == [] and backup is None
    assert routes.store('苹果').path.read_bytes() == before
    assert not (tmp_path / '路线回收站').exists()


def test_clear_refuses_points_changed_since_confirmation(tmp_path):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    first = routes.store('苹果').append(SNAPSHOT, STAMP)
    routes.store('苹果').append(SNAPSHOT, STAMP)
    before = routes.store('苹果').path.read_bytes()
    with pytest.raises(ValueError, match='变化'):
        routes.clear_points('苹果', expected_data=first)
    assert routes.store('苹果').path.read_bytes() == before
    assert not (tmp_path / '路线回收站').exists()


def test_failed_clear_write_keeps_original_and_valid_backup(tmp_path, monkeypatch):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    routes.store('苹果').append(SNAPSHOT, STAMP)
    before = routes.store('苹果').path.read_bytes()
    monkeypatch.setattr('utils.drivers.coordinate_point_store.os.replace',
                        lambda *args: (_ for _ in ()).throw(PermissionError('denied')))
    with pytest.raises(PermissionError):
        routes.clear_points('苹果')
    assert routes.store('苹果').path.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))
    backups = list((tmp_path / '路线回收站').glob('*.json'))
    assert len(backups) == 1 and backups[0].read_bytes() == before


def test_failed_backup_cannot_clear_original(tmp_path, monkeypatch):
    routes = PointRoutes(tmp_path / '我的点位.json')
    routes.create('苹果')
    routes.store('苹果').append(SNAPSHOT, STAMP)
    before = routes.store('苹果').path.read_bytes()
    from pathlib import Path
    real_open = Path.open

    def denied(path, *args, **kwargs):
        if '路线回收站' in path.parts:
            raise PermissionError('backup denied')
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(PermissionError):
        routes.clear_points('苹果')
    assert routes.store('苹果').path.read_bytes() == before


def test_corrupt_route_clear_preserves_original_without_backup(tmp_path):
    path = tmp_path / '苹果.json'
    path.write_text('{bad', encoding='utf-8')
    routes = PointRoutes(tmp_path / '我的点位.json')
    with pytest.raises(ValueError):
        routes.clear_points('苹果')
    assert path.read_text(encoding='utf-8') == '{bad'
    assert not (tmp_path / '路线回收站').exists()
