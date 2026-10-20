"""Typed identities and live availability for dynamic advanced gather objects."""
import hashlib
import struct


NAMESPACE = 'XDTLevelAndEntity.Gameplay.Component.Gather'
SPECIAL_COMPONENTS = {
    ('CollectableAdvancedStoneComponent', NAMESPACE): ('fluorite', 'AdvancedStoneComponentData'),
    ('CollectableAdvancedTreeComponent', NAMESPACE): ('roaming_oak', 'AdvancedTreeComponentData'),
}
# Verified beside the user's identified resources on 2026-10-20. Coordinates
# are deliberately absent: these are identities, not fixed spawn locations.
VERIFIED_SPECIAL_IDS = {'fluorite': (42002, 402), 'roaming_oak': (42001, 401)}


def special_gather(meta, component, entity):
    info = meta.obj(component)
    expected = SPECIAL_COMPONENTS.get((info['name'], info['namespace']))
    if expected is None:
        return None
    if meta.ref(component, '<entity>k__BackingField') != entity:
        raise ValueError('高级资源归属不符')
    category, data_name = expected
    field = meta.field(info['address'], '_componentData')
    data_type = meta.cls(field['type'])
    if field['code'] != 17 or field['flags'] & 16 or (data_type['name'], data_type['namespace']) != (
            data_name, 'ScriptsRefactory.DataAndProtocol.ComponentsData'):
        raise ValueError('高级资源数据类型不符')
    sid = meta.field(data_type['address'], 'staticId')
    if sid['code'] != 8 or sid['flags'] & 16 or not 16 <= sid['offset'] <= data_type['size']-4 \
            or not 16 <= field['offset'] <= info['size']-(data_type['size']-16):
        raise ValueError('高级资源编号布局不符')
    at = component+field['offset']+sid['offset']-16
    static_id = meta.integer(at)
    table = meta.ref(component, '_tableConfig')
    ti = meta.obj(table)
    if (ti['name'], ti['namespace']) != ('TableAdvcollectable', ''):
        raise ValueError('高级资源配置类型不符')
    tf = meta.field(ti['address'], 'id')
    if tf['code'] != 8 or tf['flags'] & 16 or not 16 <= tf['offset'] <= ti['size']-4:
        raise ValueError('高级资源配置编号布局不符')
    if static_id != VERIFIED_SPECIAL_IDS[category][0] or meta.integer(table+tf['offset']) != static_id:
        raise ValueError('高级资源配置编号不符')
    collectable = meta.ref(component, '_collectable')
    ci = meta.obj(collectable)
    if (ci['name'], ci['namespace']) != ('CollectableObjectComponent', NAMESPACE) \
            or meta.ref(collectable, '<entity>k__BackingField') != entity \
            or meta.ref(collectable, '_proxy') != component:
        raise ValueError('高级资源采集状态归属不符')
    item = meta.field(ci['address'], 'itemTypeID')
    if item['code'] != 8 or item['flags'] & 16 or not 16 <= item['offset'] <= ci['size']-4:
        raise ValueError('高级资源物品编号布局不符')
    item_at = collectable+item['offset']
    if meta.integer(item_at) != VERIFIED_SPECIAL_IDS[category][1]:
        raise ValueError('高级资源物品编号不符')
    cf = meta.field(ci['address'], '_componentData')
    ct = meta.cls(cf['type'])
    if cf['code'] != 17 or cf['flags'] & 16 or (ct['name'], ct['namespace']) != (
            'CollectableObjectData', 'ScriptsRefactory.DataAndProtocol.ComponentsData') \
            or not 16 <= cf['offset'] <= ci['size']-(ct['size']-16):
        raise ValueError('高级资源采集状态布局不符')
    status_at = collectable+cf['offset']
    raw = meta.data(status_at, ct['size']-16)
    def value(name, code, width, fmt):
        f = meta.field(ct['address'], name)
        if f['code'] != code or f['flags'] & 16 or not 16 <= f['offset'] <= ct['size']-width:
            raise ValueError('高级资源采集计数字段不符')
        return struct.unpack_from(fmt, raw, f['offset']-16)[0]
    available = value('availableNum', 8, 4, '<i')
    cold_end = value('coldEndTime', 10, 8, '<q')
    if not 0 <= available <= 100 or cold_end < 0:
        raise ValueError('高级资源采集状态范围无效')
    # A digest of the whole collectable-state struct is evidence only: it tells
    # a later press-apart comparison that some field moved, never that the
    # player collected something. Parsed fields stay the only verdict inputs.
    digest = hashlib.blake2b(raw, digest_size=8).hexdigest()
    if (meta.integer(at) != static_id or meta.integer(table+tf['offset']) != static_id
            or meta.integer(item_at) != VERIFIED_SPECIAL_IDS[category][1]
            or meta.ref(component, '_tableConfig') != table
            or meta.ref(component, '_collectable') != collectable
            or meta.ref(component, '<entity>k__BackingField') != entity
            or meta.ref(collectable, '<entity>k__BackingField') != entity
            or meta.ref(collectable, '_proxy') != component
            or meta.data(status_at, len(raw)) != raw):
        raise ValueError('高级资源采样时发生变化')
    return {'category': category, 'resource_static_id': static_id,
            'resource_available': available, 'resource_cold_end_ms': cold_end,
            'resource_state_digest': digest,
            'resource_component': component, 'resource_entity': entity,
            'map_dynamic': True}
