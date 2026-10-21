"""Point persistence with native process locking and atomic replacement."""
import json
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import tempfile

from utils.core.coordinate_points import append_point, empty_points, set_collect_wait, validate_points, remove_point


def _lock_file(stream, acquire):
    """Keep nonblocking, process-scoped exclusion on Windows and POSIX."""
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN)


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent, prefix=path.stem + '-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class PointStore:
    def __init__(self, path, require_existing=False):
        self.path = Path(path)
        self.require_existing = require_existing

    def load(self):
        try:
            text = self.path.read_text(encoding='utf-8-sig')
        except FileNotFoundError:
            if self.require_existing:
                raise ValueError('路线文件已被移动或删除，请重新选择路线。') from None
            return empty_points()
        return validate_points(json.loads(text))

    def append(self, snapshot, saved_at, collect_wait=None):
        return self._update(lambda data: append_point(data, snapshot, saved_at, collect_wait=collect_wait))

    def set_collect_wait(self, point_id, seconds, expected_data=None):
        def change(data):
            if expected_data is not None and data != expected_data:
                raise ValueError('点位已发生变化，请重新打开点位管理后再保存。')
            return set_collect_wait(data, point_id, seconds)
        return self._update(change)

    def delete_point(self, point_id, backup_path, expected_data=None):
        with self.locked():
            data = self.load()
            if expected_data is not None and data != expected_data:
                raise ValueError('点位已发生变化，请重新打开点位管理后再删除。')
            result = remove_point(data, point_id)
            backup_path = Path(backup_path)
            if backup_path.resolve() == self.path.resolve():
                raise ValueError('备份不能覆盖当前路线文件。')
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            with backup_path.open('xb') as stream:
                stream.write(self.path.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            self._write(result)
            return result, backup_path

    def create(self, data=None):
        data = deepcopy(validate_points(data if data is not None else empty_points()))
        with self.locked():
            if self.path.exists():
                raise ValueError('同名路线已存在，请换一个名称。')
            self._write(data)
        return data

    def move_to(self, target):
        target = PointStore(target)
        with self.locked(), target.locked():
            self.load()
            if target.path.exists():
                raise ValueError('同名路线已存在，请换一个名称。')
            os.rename(self.path, target.path)

    def clear(self, backup_path, expected_data=None):
        """Back up the locked current file before atomically removing its points."""
        with self.locked():
            data = self.load()
            if expected_data is not None and data != expected_data:
                raise ValueError('点位已发生变化，请重新核对后再清空。')
            if not data['points']:
                return data, None
            backup_path = Path(backup_path)
            if backup_path.resolve() == self.path.resolve():
                raise ValueError('备份不能覆盖当前路线文件。')
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            raw = self.path.read_bytes()
            created = False
            try:
                with backup_path.open('xb') as stream:
                    created = True
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                if created:
                    backup_path.unlink(missing_ok=True)
                raise
            result = deepcopy(data)
            result['points'] = []
            self._write(result)
            return result, backup_path

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The OS releases this lock even if the process terminates.
        # Re-read inside the lock so two monitor windows cannot lose a save.
        with self.path.with_suffix('.lock').open('a+b') as lock:
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            try:
                _lock_file(lock, True)
            except OSError as exc:
                raise OSError('另一个窗口正在保存点位，请稍后再试。') from exc
            try:
                yield
            finally:
                _lock_file(lock, False)

    def _update(self, change):
        with self.locked():
            data = change(self.load())
            self._write(data)
            return data

    def _write(self, data):
        atomic_write_json(self.path, data)
