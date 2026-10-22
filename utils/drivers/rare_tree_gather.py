"""Owned, live gather identity for the user-verified rare-timber tree (211)."""
import struct

NAMESPACE = 'XDTLevelAndEntity.Gameplay.Component.Gather'
DATA_NAMESPACE = 'ScriptsRefactory.DataAndProtocol.ComponentsData'
RARE_TREE_TYPE = 211


def rare_tree_gather(meta, owner):
    entity = meta.ref(owner, '<entity>k__BackingField')
    container = meta.ref(entity, '_components')
    components = meta.dictionary(container, max_items=128)
    def selected(name):
        return [p for p in components if (meta.obj(p)['name'], meta.obj(p)['namespace']) == (name, NAMESPACE)]
    trees = selected('CollectableTreeComponent')
    if len(trees) != 1:
        return {}
    tree = trees[0]
    info = meta.obj(tree)
    def field(ci, name, code, width):
        f = meta.field(ci['address'], name)
        if f['code'] != code or f['flags'] & 16 or not 16 <= f['offset'] <= ci['size']-width:
            raise ValueError('珍稀木材树字段布局不符')
        return f
    item_at = tree+field(info, '_itemTypeID', 8, 4)['offset']
    if meta.integer(item_at) != RARE_TREE_TYPE:
        return {}
    if meta.ref(tree, '<entity>k__BackingField') != entity:
        raise ValueError('珍稀木材树归属不符')
    data = field(info, '_componentData', 17, 4)
    ti = meta.cls(data['type'])
    if (ti['name'], ti['namespace']) != ('TreeComponentData', DATA_NAMESPACE):
        raise ValueError('珍稀木材树身份类型不符')
    resource_at = tree+data['offset']+field(ti, 'resourceId', 9, 4)['offset']-16
    resource_id = struct.unpack('<I', meta.data(resource_at, 4))[0]
    collectables = selected('CollectableObjectComponent')
    if resource_id <= 0 or len(collectables) != 1:
        raise ValueError('珍稀木材树身份或采集器不唯一')
    collectable = collectables[0]
    ci = meta.obj(collectable)
    if meta.ref(collectable, '<entity>k__BackingField') != entity or meta.ref(collectable, '_proxy') != tree:
        raise ValueError('珍稀木材树采集器归属不符')
    collect_item_at = collectable+field(ci, 'itemTypeID', 8, 4)['offset']
    if meta.integer(collect_item_at) != RARE_TREE_TYPE:
        raise ValueError('珍稀木材树采集器编号不符')
    cf = field(ci, '_componentData', 17, 4)
    ct = meta.cls(cf['type'])
    if (ct['name'], ct['namespace']) != ('CollectableObjectData', DATA_NAMESPACE) \
            or cf['offset']+ct['size']-16 > ci['size']:
        raise ValueError('珍稀木材树状态布局不符')
    at = collectable+cf['offset']
    raw = meta.data(at, ct['size']-16)
    available = struct.unpack_from('<i', raw, field(ct, 'availableNum', 8, 4)['offset']-16)[0]
    cold_end = struct.unpack_from('<q', raw, field(ct, 'coldEndTime', 10, 8)['offset']-16)[0]
    if not 0 <= available <= 3 or cold_end < 0:
        raise ValueError('珍稀木材树采集次数无效')
    if meta.data(at, len(raw)) != raw or meta.integer(item_at) != RARE_TREE_TYPE \
            or meta.integer(collect_item_at) != RARE_TREE_TYPE \
            or struct.unpack('<I', meta.data(resource_at, 4))[0] != resource_id \
            or meta.ref(owner, '<entity>k__BackingField') != entity \
            or meta.ref(entity, '_components') != container \
            or meta.dictionary(container, max_items=128) != components \
            or meta.ref(tree, '<entity>k__BackingField') != entity \
            or meta.ref(collectable, '<entity>k__BackingField') != entity \
            or meta.ref(collectable, '_proxy') != tree:
        raise ValueError('珍稀木材树采样时发生变化')
    return {'resource_id': resource_id, 'resource_item_type': RARE_TREE_TYPE,
            'resource_available': available, 'resource_cold_end_ms': cold_end}
