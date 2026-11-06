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
        if data is not None and 'reordered_route' in data:
            from copy import deepcopy
            from utils.core.reordered_route import isolation_id
            isolation_id(data)
            data=deepcopy(data)
            data['reordered_route']['isolation_id']=uuid4().hex
        PointStore(self.path(name)).create(data)
        return name

    def create_reordered(self, source, name, order, whitelist, expected_sha256=None):
        """Publish one atomic candidate with provenance; never select or alter source."""
        import hashlib
        from copy import deepcopy
        from utils.core.reordered_route import point_digest
        source_store=self.store(source)
        target=self.path(name)
        if any(n.casefold()==name.casefold() for n in self.names()):
            raise ValueError('同名路线已存在')
        with source_store.locked():
            raw=source_store.path.read_bytes()
            digest=hashlib.sha256(raw).hexdigest()
            if expected_sha256 is not None and digest!=expected_sha256:
                raise ValueError('原路线已变化，请重新生成排序建议')
            data=source_store.load();points=data['points'];count=len(points)
            if not isinstance(order,list) or any(type(i)is not int for i in order) or sorted(order)!=list(range(1,count+1)):
                raise ValueError('重排必须是全部原点位的排列')
            if not isinstance(whitelist,(list,set,tuple)) or any(type(i)is not int or not 1<=i<=count for i in whitelist):
                raise ValueError('换序白名单无效')
            duplicates={tuple(p['position']) for p in points if sum(q['position']==p['position'] for q in points)>1}
            locked={p['id'] for p in points if p['id'] not in whitelist or 'special_resource' in p or tuple(p['position']) in duplicates}
            # Locked anchors partition independently reorderable intervals.
            for new,old in enumerate(order,1):
                if old in locked and new!=old or any(min(new,old)<anchor<max(new,old) for anchor in locked):
                    raise ValueError('换序跨越或移动了锁定锚点')
            result=deepcopy(data)
            result['points']=[deepcopy(points[old-1]) for old in order]
            for new,p in enumerate(result['points'],1):p['id']=new
            result['reordered_route']={'schema':'reordered-route-v1','isolation_id':uuid4().hex,
                'source_sha256':digest,'source_name':source,'new_to_old':order,
                'source_points':{str(p['id']):point_digest(p) for p in points}}
            PointStore(target).create(result)
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
