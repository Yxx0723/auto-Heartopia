"""Persist live blueberry observations separately from executable routes."""
from datetime import datetime
import json
import math
import os
from pathlib import Path
from uuid import uuid4

from utils.core import thresholds as policy
from utils.core.coordinate_points import append_point, empty_points, validate_points, valid_stamp
from utils.core.coordinate_route import valid_position
from utils.drivers.coordinate_point_store import PointStore, atomic_write_json

# Conservative match for the same stationary bush on a subsequent observation.
MATCH_RADIUS = .15


def nearby(a, b):
    return math.dist(a, b) <= MATCH_RADIUS


class BlueberrySurvey:
    def __init__(self, directory):
        self.path = Path(directory)/'.蓝莓调查.json'
        self.lock = PointStore(self.path)

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            return []
        if not isinstance(data, dict) or data.get('schema') != 'blueberry-survey-v1':
            raise ValueError('蓝莓调查记录格式不支持')
        rows = data.get('resources')
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not valid_position(row.get('position'))
            or not valid_stamp(row.get('seen_at')) or not isinstance(row.get('version'), str)
            or row.get('scene') != 'startown' for row in rows
        ):
            raise ValueError('蓝莓调查记录不完整')
        return rows

    def observe(self, snapshot):
        if snapshot.get('survey_scene') != 'startown' or not snapshot.get('survey_version'):
            raise ValueError('当前地图归属或版本尚未确认')
        positions = [obj['position'] for obj in snapshot['objects']
                     if obj.get('category') == 'blueberry' and obj.get('map_static') is True
                     and valid_position(obj.get('position'))]
        stamp = datetime.now().astimezone().isoformat()
        if not positions:
            return 0
        with self.lock.locked():
            rows = self.load()
            changed = False
            for position in positions:
                matches = [row for row in rows if row['scene'] == 'startown' and nearby(row['position'], position)]
                if len(matches) > 1:
                    continue  # Ambiguous proximity cannot overwrite either observation.
                if matches:
                    row = matches[0]
                    if row['version'] != snapshot['survey_version']:
                        row.pop('verification', None)
                        row.update(seen_at=stamp, version=snapshot['survey_version'])
                        changed = True
                else:
                    rows.append(dict(scene='startown', position=list(position), seen_at=stamp,
                                     version=snapshot['survey_version']))
                    changed = True
            if changed:
                atomic_write_json(self.path, {'schema': 'blueberry-survey-v1', 'resources': rows})
        return len(positions)

    def verify_stand(self, snapshot, stand, resource):
        """Record a user-tested stand after rechecking the live bush and geometry."""
        from utils.core.coordinate_navigation import ObstacleMap
        if (not valid_position(stand) or not valid_position(resource)
                or math.dist(stand, resource) > policy.COORD_NAV_APPROACH_RADIUS
                or abs(stand[1] - resource[1]) > policy.COORD_APPROACH_HEIGHT_BAND):
            raise ValueError('站位与蓝莓距离或高度差过大')
        live = [obj for obj in snapshot['objects'] if obj.get('category') == 'blueberry'
                and obj.get('map_static') is True and valid_position(obj.get('position'))
                and nearby(obj['position'], resource)]
        if len(live) != 1:
            raise ValueError('当前快照未唯一确认这株蓝莓')
        xz = (stand[0], stand[2])
        if not ObstacleMap(snapshot, stand).clear(xz, xz):
            raise ValueError('站位未通过碰撞或水域检查')
        with self.lock.locked():
            rows = self.load()
            matches = [r for r in rows if r['scene'] == snapshot['survey_scene']
                       and r['version'] == snapshot['survey_version'] and nearby(r['position'], resource)]
            if len(matches) != 1:
                raise ValueError('调查记录已变化，请重新扫描')
            matches[0]['verification'] = {
                'stand': list(stand), 'at': datetime.now().astimezone().isoformat(),
                'evidence': 'manual_walk_and_harvest'}
            atomic_write_json(self.path, {'schema': 'blueberry-survey-v1', 'resources': rows})

    @staticmethod
    def verified(row, version):
        v = row.get('verification')
        return (row['version'] == version and isinstance(v, dict)
                and v.get('evidence') == 'manual_walk_and_harvest'
                and valid_stamp(v.get('at')) and valid_position(v.get('stand'))
                and math.dist(row['position'], v['stand']) <= policy.COORD_NAV_APPROACH_RADIUS
                and abs(row['position'][1] - v['stand'][1]) <= policy.COORD_APPROACH_HEIGHT_BAND)

    def preview(self, route, snapshot):
        validate_points(route)
        pending, duplicates, additions = 0, 0, []
        positions = [p['position'] for p in route['points']]
        for row in self.load():
            if not self.verified(row, snapshot['survey_version']):
                pending += 1
                continue
            stand = row['verification']['stand']
            if any(nearby(stand, position) for position in positions):
                duplicates += 1
                continue
            # Revalidate live identity and collision at import time. Old
            # observations remain visible but cannot authorize a route edit.
            live = [obj for obj in snapshot['objects'] if obj.get('category') == 'blueberry'
                    and obj.get('map_static') is True and valid_position(obj.get('position'))
                    and nearby(obj['position'], row['position'])]
            if len(live) != 1:
                pending += 1
                continue
            from utils.core.coordinate_navigation import ObstacleMap
            xz = (stand[0], stand[2])
            if not ObstacleMap(snapshot, stand).clear(xz, xz):
                pending += 1
                continue
            positions.append(stand)
            additions.append(row['verification'])
        return additions, duplicates, pending

    def update_route(self, store, expected, additions):
        if not additions:
            return None
        with store.locked():
            existed = store.path.exists()
            original = store.load() if existed else empty_points()
            if original != expected:
                raise ValueError('蓝莓路线已变化，请重新预览')
            if 'reordered_route' in original:
                raise ValueError('当前蓝莓路线包含重排元数据，请先另存普通路线')
            result = original
            # Keep the known route intact. No straight-line sorting across
            # unverified edges; new stands follow the order of field verification.
            for item in sorted(additions, key=lambda v: v['at']):
                result = append_point(result, {'position': item['stand'], 'sample_at': item['at']}, item['at'])
            validate_points(result)
            backup = None
            if existed:
                backup = store.path.parent/'路线回收站'/(
                    f'蓝莓-调查更新前-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.json')
                backup.parent.mkdir(exist_ok=True)
                raw = store.path.read_bytes()
                with backup.open('xb') as f:
                    f.write(raw)
                    f.flush()
                    os.fsync(f.fileno())
            atomic_write_json(store.path, result)
            return backup
