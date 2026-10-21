"""Session/day-scoped last observed positions, used only as search hints."""
from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path
import time

from utils.core.coordinate_route import valid_position
from utils.core.special_approach import in_interaction_range
from utils.core import thresholds as policy
from utils.drivers.coordinate_point_store import atomic_write_json


class SpecialResourceLocations:
    def __init__(self, path, wall_clock=None, clock=time.monotonic):
        self.path = Path(path)
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        self.clock, self.missing_since = clock, {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            self.data = data if isinstance(data, dict) and data.get('schema') == 'special-search-v1' else {}
        except (OSError, ValueError):
            self.data = {}

    def enrich(self, scene):
        context = scene.get('global_special_context')
        if scene.get('state') != 'ready' or scene.get('global_special_state') != 'ready' \
                or not isinstance(context, (list, tuple)) or len(context) != 3 \
                or any(type(v) is not int or v <= 0 for v in context):
            return scene
        today = self.wall_clock().date().isoformat()
        data = self.data
        if data.get('context') != list(context) or data.get('day') != today:
            data = {'schema': 'special-search-v1', 'context': list(context), 'day': today, 'targets': {}}
            self.missing_since.clear()
        data = deepcopy(data)
        if not isinstance(data.get('targets'), dict):
            data['targets'] = {}
        if not isinstance(data.get('rejected'), dict):
            data['rejected'] = {}
        current = scene.get('global_special_resources', ())
        for obj in current:
            kind = obj.get('category')
            static = {'roaming_oak': 42001, 'fluorite': 42002}.get(kind)
            if not static or obj.get('resource_static_id') != static \
                    or obj.get('resource_global_validated') is not True or not valid_position(obj.get('position')):
                continue
            data['targets'][kind] = {'category': kind, 'resource_static_id': static,
                'position': list(obj['position']), 'resource_global_only': True,
                'resource_search_hint': True, 'map_dynamic': True,
                'record_read_at': self.wall_clock().replace(second=0, microsecond=0).isoformat()}
        for kind, obj in list(data['targets'].items()):
            if not isinstance(obj, dict) or not valid_position(obj.get('position')):
                continue
            live = [o for o in scene.get('objects', ()) if o.get('category') == kind
                    and o.get('resource_static_id') == obj.get('resource_static_id')
                    and o.get('map_dynamic') is True and not o.get('map_cached')
                    and type(o.get('resource_available')) is int and valid_position(o.get('position'))]
            if live:
                data['rejected'].pop(kind, None)
                self.missing_since.pop(kind, None)
                continue
            blocked = data['rejected'].get(kind)
            if isinstance(blocked, dict) and valid_position(blocked.get('position')):
                if math.dist(blocked['position'], obj['position']) > .3:
                    data['rejected'].pop(kind, None)
                else:
                    del data['targets'][kind]
                    continue
            if valid_position(scene.get('position')) and in_interaction_range(scene['position'], obj['position']):
                position, since = self.missing_since.setdefault(kind, (list(obj['position']), self.clock()))
                if math.dist(position, obj['position']) > .3:
                    self.missing_since[kind] = (list(obj['position']), self.clock())
                elif self.clock()-since >= policy.COORD_SPECIAL_LOAD_WAIT_SECONDS:
                    data['rejected'][kind] = {'position': list(obj['position']), 'reason': 'nearby-model-not-observed'}
                    del data['targets'][kind]
            else:
                self.missing_since.pop(kind, None)
        if data != self.data:
            self.data = data
            try:
                atomic_write_json(self.path, data)
            except OSError:
                pass  # A cache failure must not discard current scene/collision data.
        hints = [{'category': kind, 'resource_static_id': obj['resource_static_id'],
                  'position': list(obj['position']), 'resource_global_only': True,
                  'resource_search_hint': True, 'map_dynamic': True} for kind, obj in data['targets'].items()
                 if isinstance(obj, dict) and kind not in {o.get('category') for o in current}
                 and obj.get('resource_static_id') == {'roaming_oak': 42001, 'fluorite': 42002}.get(kind)
                 and valid_position(obj.get('position'))]
        visible = [o for o in current if o.get('category') not in data['rejected']]
        return {**scene, 'global_special_resources': visible, 'special_search_hints': hints,
                'special_resource_status': {kind: 'unconfirmed-position' for kind in data['rejected']}}
