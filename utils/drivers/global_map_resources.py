"""Current synchronized resource/creature entities; not a list of spawn candidates."""
import struct

from utils.core.coordinate_route import valid_position
from utils.drivers.global_special_resources import GlobalSpecialReader, decode_marker


FAUNA = {'ServerInsectComponent': ('insect', 'InsectStaticId'),
         'ServerBirdComponent': ('bird', 'BirdId'),
         'ServerFishComponent': ('fish', 'FishStaticId')}
# Gather identities already checked against actual player-selected objects.
GATHER_TYPES = {101: 'bush', 102: 'blueberry', 103: 'raspberry', 301: 'rock', 302: 'ore',
                203: 'tree', 206: 'tree', 207: 'tree'}


def decode_resource(values, index, generation):
    position = list(struct.unpack('<3f', values['LocalPositionComponent']))
    if not valid_position(position) or not generation:
        raise ValueError('地图资源坐标失效')
    identities = [name for name in FAUNA if name in values]
    if len(identities) > 1:
        return None
    marker = {'position': position, 'map_global': True, 'map_dynamic': True,
              'resource_global_validated': True, 'resource_global_only': True,
              'source': 'global-resource-ecs', 'map_id': ('global-resource', index, generation)}
    if identities:
        name = identities[0]
        static_id = struct.unpack_from('<i', values[name])[0]
        if static_id <= 0:
            return None
        if name == 'ServerFishComponent':
            shadow = values.get('FishShadowComponent')
            # Hidden or unmatched fish shadows must not appear as catchable fish.
            if shadow is None or struct.unpack_from('<i', shadow)[0] != static_id or shadow[4] != 0:
                return None
        return {**marker, 'category': FAUNA[name][0], 'resource_static_id': static_id,
                'interaction_state': 'unknown'}
    resource = values.get('MapResourcePointComponent')
    if resource is None:
        return None
    resource_id, item_type, static_id = struct.unpack('<Iii', resource)
    if item_type in (401, 402):
        dynamic = values.get('DynamicMapEntityComponent')
        return decode_marker(resource, dynamic, values['LocalPositionComponent'], index, generation) if dynamic else None
    if resource_id <= 0 or item_type <= 0:
        return None
    return {**marker, 'category': GATHER_TYPES.get(item_type, 'resource'),
            'resource_id': resource_id, 'resource_item_type': item_type,
            'resource_static_id': static_id, 'interaction_state': 'unknown'}


class GlobalMapResourceReader(GlobalSpecialReader):
    extra_types = {'ServerInsectComponent': ('XDT.Scene.Shared.Modules.InsectCatching', 32),
                   'ServerBirdComponent': ('XDT.Scene.Shared.Modules.BirdWatching', 32),
                   'ServerFishComponent': ('XDT.Scene.Shared.GamePlay.Fishing', 32),
                   'FishShadowComponent': ('XDT.Scene.Shared.Creatures', 24)}
    extra_layouts = {name: [(field, 8, 16)] for name, (_, field) in FAUNA.items()}
    extra_layouts['FishShadowComponent'] = [('FishId', 8, 16), ('IsHide', 2, 20)]

    def select_components(self, indices, bindings):
        present = [name for name, binding in bindings.items() if binding[0] in indices]
        if 'LocalPositionComponent' not in present or not any(
                name in present for name in ('MapResourcePointComponent', *FAUNA)):
            return []
        return present

    def decode_values(self, values, index, generation):
        return decode_resource(values, index, generation)
