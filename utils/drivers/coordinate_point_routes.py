"""Each named route remains an independent point JSON beside the executable."""
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from utils.core.coordinate_point_routes import validate_route_name
from utils.drivers.coordinate_point_store import PointStore, atomic_write_json


class PointRoutes:
    def __init__(self, default_path):
        self.default_path = Path(default_path)
        self.root = self.default_path.parent
        self.default_name = validate_route_name(self.default_path.stem)
        self.selection_path = self.root / '.路线选择.json'

    def path(self, name):
        name = validate_route_name(name)
        path = self.root / (name + '.json')
        if path.resolve().parent != self.root.resolve():
            raise ValueError('路线文件必须位于点位文件夹内。')
        return path

    def names(self):
        result = []
        for path in self.root.glob('*.json'):
            if path.name.startswith('.') or not path.is_file():
                continue
            try:
                self.path(path.stem)
            except ValueError:
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8-sig'))
                if not isinstance(data, dict) or data.get('schema') != 'coordinate-points-v1':
                    continue
            except (OSError, ValueError):
                pass  # Keep damaged routes visible so selecting one reports the error.
            result.append(path.stem)
        return sorted(result, key=lambda name: (name != self.default_name, name.casefold())) \
            or [self.default_name]

    def store(self, name):
        path = self.path(name)
        if name not in self.names():
            raise ValueError('所选路线不存在，请重新选择。')
        return PointStore(path, require_existing=path.exists())

    def selected_name(self):
        names = self.names()
        try:
            data = json.loads(self.selection_path.read_text(encoding='utf-8-sig'))
            selected = data.get('selected_route') if isinstance(data, dict) else None
        except (OSError, ValueError):
            selected = None
        return selected if selected in names else names[0]

    def remember(self, name):
        if name not in self.names():
            raise ValueError('所选路线不存在。')
        atomic_write_json(self.selection_path, {'selected_route': name})

    def create(self, name, data=None):
        name = validate_route_name(name)
        if any(existing.casefold() == name.casefold() and self.path(existing).exists()
               for existing in self.names()):
            raise ValueError('同名路线已存在，请换一个名称。')
        PointStore(self.path(name)).create(data)
        return name

    def rename(self, source, name):
        name = validate_route_name(name)
        if any(existing.casefold() == name.casefold() for existing in self.names()):
            raise ValueError('同名路线已存在，请换一个名称。')
        store = self.store(source)
        if store.path.exists():
            store.move_to(self.path(name))
        else:
            self.create(name, store.load())
        return name

    def archive(self, name):
        store = self.store(name)
        if not store.path.exists():
            raise ValueError('当前是尚未保存的空路线。')
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        target = self.root / '路线回收站' / f'{validate_route_name(name)}-{stamp}-{uuid4().hex[:8]}.json'
        store.move_to(target)
        return target

    def clear_points(self, name, expected_data=None):
        store = self.store(name)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = self.root / '路线回收站' / f'{validate_route_name(name)}-清空前-{stamp}-{uuid4().hex[:8]}.json'
        return store.clear(backup, expected_data=expected_data)
