"""Display-only merging of live resources and full-map definition references."""
from utils.core.coordinate_route import valid_position


def combine_resource_markers(objects, definitions):
    def key(obj):
        position = obj.get('position')
        if obj.get('category') not in ('rock', 'ore') or not valid_position(position):
            return None
        return obj['category'], *(round(v, 2) for v in position)

    references = {key(obj): obj for obj in definitions if obj.get('map_definition') is True and key(obj) is not None}
    result = []
    live_keys = set()
    for obj in objects:
        identity = key(obj)
        if identity not in references:
            result.append(obj)
        elif not obj.get('map_cached'):
            result.append({**obj, 'map_global': True})
            live_keys.add(identity)
    result.extend({**obj, 'map_global': True} for identity, obj in references.items() if identity not in live_keys)
    return result
