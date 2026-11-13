import json

import pytest

from utils.drivers.cooking_record_paths import resolve_cooking_record_dir
from tests.unit.test_cooking_entry_control import control


@pytest.mark.parametrize('name', ['Alice', '林小镇', 'Alice Smith'])
def test_record_dir_uses_current_user_not_install_location(tmp_path, monkeypatch, name):
    home = tmp_path/'Profiles'/name
    local = home/'AppData'/'Local'
    install = tmp_path/'Program Files'
    install.mkdir()
    monkeypatch.chdir(install)
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    expected = local/'auto-Heartopia'/'烹饪记录'
    assert resolve_cooking_record_dir(home=home) == expected
    assert not expected.exists()
    c, game, *_ = control(expected)
    c.start(123)
    c.thread.join(3)
    assert c.path.parent == expected
    assert {'entry.json', 'events.jsonl', 'summary.json'} <= {p.name for p in c.path.iterdir()}
    assert not game.keys
    assert not list(install.iterdir())


@pytest.mark.parametrize('local', [None, 'relative/path'])
def test_unavailable_localappdata_uses_home(tmp_path, monkeypatch, local):
    if local is None:
        monkeypatch.delenv('LOCALAPPDATA', raising=False)
    else:
        monkeypatch.setenv('LOCALAPPDATA', local)
    assert resolve_cooking_record_dir(home=tmp_path) == tmp_path/'AppData'/'Local'/'auto-Heartopia'/'烹饪记录'


def journal(root, name, **changes):
    path = root/name/'entry.json'
    path.parent.mkdir(parents=True)
    record = dict(schema='cooking-entry-p0-v1', binding='p0-game-123',
                  entry_dispatched=True, state='UNCONFIRMED')
    path.write_text(json.dumps(record | changes), encoding='utf-8')
    return path


def test_restart_resumes_legacy_entry_without_sending_key(tmp_path):
    old, new = tmp_path/'Desktop'/'烹饪记录', tmp_path/'local'/'烹饪记录'
    pending = journal(old, '入口验证-old')
    history = journal(new, '入口验证-new', state='RECIPE_VALIDATED')
    before = history.read_bytes()
    for _ in range(2):
        c, game, *_ = control(new)
        c.legacy_entry_root = old
        c.start(123)
        c.thread.join(3)
        assert not c.active and not game.events
        assert c.entry_journal('p0-game-123') == pending
        assert c.path.parent == new
        assert history.read_bytes() == before
        assert pending.is_file()


@pytest.mark.parametrize('problem', ['duplicate', 'other-session', 'invalid-json', 'redirect'])
def test_ambiguous_legacy_journals_stop_before_input(tmp_path, problem):
    old, new = tmp_path/'Desktop'/'烹饪记录', tmp_path/'local'/'烹饪记录'
    if problem == 'redirect':
        old.parent.mkdir()
        old.write_text('not a directory', encoding='utf-8')
    else:
        path = journal(old, '入口验证-old',
                       binding='p0-game-999' if problem == 'other-session' else 'p0-game-123')
        if problem == 'duplicate':
            journal(new, '入口验证-new')
        if problem == 'invalid-json':
            path.write_text('{', encoding='utf-8')
    c, game, *_ = control(new)
    c.legacy_entry_root = old
    c.start(123)
    c.thread.join(3)
    assert not game.events
    assert c.take_latest()['state'] == 'error'


def test_unwritable_record_root_fails_before_input(tmp_path, monkeypatch):
    from pathlib import Path
    root = tmp_path/'denied'
    c, game, *_ = control(root)
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        if path.parent == root:
            raise PermissionError('denied')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    c.start(123)
    c.thread.join(3)
    assert not game.events
    result = c.take_latest()
    assert result['state'] == 'error'
    assert '无法创建烹饪记录目录' in result['message']


@pytest.mark.parametrize('mode,prefix,expected', [
    ('selection', '选菜-', {'entry.json', 'events.jsonl'}),
    ('pot', '单锅-', {'pot.json', 'events.jsonl'}),
    ('batch', '连续制作-', {'batch.json', 'events.jsonl'}),
])
def test_record_writers_use_same_user_root(tmp_path, monkeypatch, mode, prefix, expected):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from utils.flows import cooking_batch
    from utils.ui import cooking_selection_control, cooking_single_pot_control

    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'账户 AppData'))
    root = resolve_cooking_record_dir(home=tmp_path)
    reader = Mock()
    device = Mock()
    controls = Mock()
    controls.snapshot.return_value = (True, False)
    window = SimpleNamespace(hwnd=1, size=lambda: (1280, 720))
    recipe = {'status': 'confirmed', 'name': '测试料理', 'recipe_id': 55, 'product_id': 55}
    stock = {'scope': 'test', 'counts': {}, 'selectable_counts': {}}
    reader.cooking_inventory_snapshot.return_value = stock
    recipe['ingredients'] = []
    monkeypatch.setattr(cooking_selection_control, 'CookingSelectionReader',
                        lambda _: SimpleNamespace(snapshot=Mock(), recipe=SimpleNamespace(snapshot=Mock())))
    monkeypatch.setattr(cooking_single_pot_control, 'SinglePotReader', lambda _: object())

    def entry(journal, **kwargs):
        journal.write_text('{"state":"MENU_OPEN"}', encoding='utf-8')
        return {'state': 'MENU_OPEN'}

    def pot(journal, **kwargs):
        journal.write_text('{"state":"finished"}', encoding='utf-8')

    def batch(directory, **kwargs):
        assert json.loads((directory/'batch.json').read_text(encoding='utf-8'))['state'] == 'batch_wait'
        (directory/'batch.json').write_text('{"state":"batch_finished"}', encoding='utf-8')

    monkeypatch.setattr(cooking_batch, 'run_batch', batch)
    c = cooking_single_pot_control.CookingSinglePotControl(
        tmp_path/'profile.json', root, window_factory=lambda _: window,
        reader_factory=lambda *a, **kw: reader, device_factory=lambda _: device,
        controls_factory=lambda *a: controls, runner=entry, selection_runner=Mock(),
        single_pot_runner=pot)
    if mode == 'selection':
        c.start_selection(123, recipe)
    elif mode == 'pot':
        c.start_pot(123, recipe)
    else:
        c.start_batch(123, recipe, 1)
    c.thread.join(3)
    assert not c.active and c.task_state != 'error'
    assert c.path.parent == root and c.path.name.startswith(prefix)
    assert expected <= {p.name for p in c.path.iterdir()}
    controls.close.assert_called_once()
    device.release_all.assert_called_once()
    reader.close.assert_called_once()


def test_f9_retains_legacy_pending_journal(tmp_path):
    from unittest.mock import Mock

    old, new = tmp_path/'Desktop'/'烹饪记录', tmp_path/'local'/'烹饪记录'
    pending = journal(old, '入口验证-old')
    before = pending.read_bytes()
    c, game, controls, reader, gate = control(new)
    c.legacy_entry_root = old
    controls.snapshot = Mock(return_value=(False, True))
    c.start(123)
    c.thread.join(3)
    assert not c.active and not game.events and not game.keys and gate.owner is None
    assert c.take_latest()['state'] == 'stopped'
    assert pending.read_bytes() == before
    controls.close.assert_called_once()
    reader.close.assert_called_once()
