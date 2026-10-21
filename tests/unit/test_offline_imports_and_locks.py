"""Exercise offline import boundaries and real interprocess lock ownership."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from utils.drivers.coordinate_point_store import PointStore


ROOT = Path(__file__).resolve().parents[2]


def child(code, *args):
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    return subprocess.run([sys.executable, '-c', code, *map(str, args)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)


def test_offline_collection_needs_no_windows_hooks_or_image_library():
    result = child('''
import importlib.abc
import sys
class NoNativeModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'win32api', 'win32gui', 'win32con',
                                     'win32process', 'pynput', 'msvcrt', 'cv2'}:
            raise ImportError('Forbidden during offline import: ' + fullname)
sys.meta_path.insert(0, NoNativeModules())
import utils.drivers.coordinate_point_store
import utils.flows.coordinate_player
import utils.ui.native_coordinate_monitor
''')
    assert result.returncode == 0, result.stderr


def test_other_process_cannot_save_until_owner_releases_lock(tmp_path):
    store = PointStore(tmp_path / 'points.json')
    store.create()
    attempt = '''
import sys
from utils.drivers.coordinate_point_store import PointStore
try:
    with PointStore(sys.argv[1]).locked():
        pass
except OSError:
    sys.exit(23)
'''
    before = store.path.read_bytes()
    with store.locked():
        assert child(attempt, store.path).returncode == 23
        assert store.path.read_bytes() == before
    result = child(attempt, store.path)
    assert result.returncode == 0, result.stderr


def test_abrupt_process_exit_releases_lock(tmp_path):
    store = PointStore(tmp_path / 'points.json')
    result = child('''
import os, sys
from utils.drivers.coordinate_point_store import PointStore
with PointStore(sys.argv[1]).locked():
    os._exit(0)
''', store.path)
    assert result.returncode == 0, result.stderr
    with store.locked():
        pass
    with pytest.raises(RuntimeError):
        with store.locked():
            raise RuntimeError('interrupted save')
    with store.locked():
        pass
