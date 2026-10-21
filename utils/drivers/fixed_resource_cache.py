"""Persistent main-map fixed resource definitions; display only, never live fauna."""
import json
from pathlib import Path

from utils.core.coordinate_route import valid_position
from utils.drivers.coordinate_point_store import atomic_write_json


STATIC_CATEGORIES = frozenset(('tree', 'bush', 'blueberry', 'raspberry', 'rock', 'ore'))


def context_key(context):
    if not isinstance(context, dict) or context.get('map') != 'main':
        return None
    build = context.get('build')
    return context if isinstance(build, str) and len(build) == 64 and all(
        c in '0123456789abcdefABCDEF' for c in build) else None


def fixed_points(records):
    if not isinstance(records, (list, tuple)) or len(records) > 100000:
        raise ValueError('固定资源缓存列表无效')
    result, seen = [], set()
    for obj in records:
        if not isinstance(obj, dict) or obj.get('category') not in STATIC_CATEGORIES:
            continue
        identity = obj.get('resource_id')
        if obj.get('map_definition') is not True or type(identity) is not int or identity <= 0 \
                or identity in seen or not valid_position(obj.get('position')):
            raise ValueError('固定资源定义无效')
        seen.add(identity)
        result.append({'resource_id': identity, 'category': obj['category'],
                       'position': list(obj['position']), 'map_definition': True,
                       'source': 'fixed-resource-cache'})
    return result


class FixedResourceCache:
    def __init__(self, path):
        self.path = Path(path)
        self.data, self.points, self.cached_points = {}, [], []
        self.last_source = None
        try:
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if isinstance(data, dict) and data.get('schema') == 'fixed-resources-v1' \
                    and context_key(data.get('context')):
                self.install({'schema': 'fixed-resources-v1', 'context': data['context'],
                              'points': fixed_points(data.get('points'))})
        except (OSError, ValueError):
            pass

    def install(self, data):
        self.data, self.points = data, data['points']
        self.cached_points = [{**obj, 'map_cached': True} for obj in self.points]

    def enrich(self, scene):
        context = context_key(scene.get('fixed_resource_context'))
        if scene.get('state') != 'ready' or context is None:
            return scene
        source = scene.get('global_resources', ())
        fresh = scene.get('global_resource_state') == 'ready' and bool(source)
        if fresh and (source is not self.last_source or self.data.get('context') != context):
            try:
                points = fixed_points(source)
            except ValueError:
                return scene
            if not points:
                return scene
            data = {'schema': 'fixed-resources-v1', 'context': dict(context), 'points': points}
            if data != self.data:
                self.install(data)
                try:
                    atomic_write_json(self.path, data)
                except OSError:
                    pass  # Cache IO must not interrupt map/navigation updates.
            self.last_source = source
        if self.data.get('context') != context or not self.points:
            return scene
        return {**scene, 'global_resources': self.points if fresh else self.cached_points,
                'fixed_resource_cache': {'state': 'ready', 'count': len(self.points), 'live_definitions': fresh}}
