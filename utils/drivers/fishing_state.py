"""Read-only fishing evidence for the audited managed assemblies."""
import hashlib
import struct

from utils.drivers.food_inventory import FoodInventoryReader

LEVEL_IMAGE = ('XDTLevelAndEntity', 7060992,
               '4b916f587ca0a44ae9700decb81ca0388591a725f1b3e404ea21ae10978675f2')
ENTITY_IMAGE = ('EcsClient', 5476864,
                '77570651238189d649d5bd82779e3186f83ee54f9627a0f8f3a2cecd2c6872a6')
FISH_ENTITY_TYPE = 30


def verify_image(meta, klass, expected, verified):
    image = meta.pointer(klass+0x40)
    raw = meta.pointer(image+0x10)
    key = (meta.domain,image,raw)
    if key not in verified:
        name,length,digest = expected
        if meta.text(meta.pointer(image+0x30))!=name:
            raise ValueError('钓鱼数据程序集归属不符')
        sha = hashlib.sha256()
        for offset in range(0,length,1024*1024):
            sha.update(meta.data(raw+offset,min(1024*1024,length-offset)))
        if sha.hexdigest()!=digest:
            raise ValueError('当前游戏程序集版本未经钓鱼状态标定')
        verified.add(key)


def player_fishing_status(meta, player, verified):
    sync = meta.ref(player,'Status')
    context = meta.obj(sync)
    if context['name']!='PlayerSyncStatus':
        raise ValueError('玩家同步状态类型不符')
    verify_image(meta,context['address'],LEVEL_IMAGE,verified)
    field = meta.field(context['address'],'FishingStatus')
    layout = meta.cls(field['type'])
    if field['code']!=17 or layout['name']!='FishingStatus' or not (
            16<=field['offset']<=context['size']-layout['size']+16):
        raise ValueError('玩家钓鱼状态布局不符')
    return sync,field['offset'],layout


def read_player_fishing(meta, binding):
    sync,base,layout = binding
    data = meta.data(sync+base,layout['size']-16)
    def value(name,fmt):
        offset = meta.field(layout['address'],name)['offset']-16
        return struct.unpack_from(fmt,data,offset)
    return {'stage':value('fishState','<i')[0], 'fish_id':value('baitingFishStaticId','<i')[0],
            'target_xyz':value('tarPos','<fff'), 'float_xyz':value('floatPos','<fff'),
            'pull_strength':value('pullStrength','<f')[0]}


def fish_inventory(memory, reader, verified):
    rows,stable,context = reader.owned_rows()
    counts,fish_ids = {},set()
    for info,row in rows:
        category = reader.meta.field(info['address'],'entityType')
        layout = reader.meta.cls(category['type'])
        if layout['name']!='EntityType' or 'fish' not in layout['fields']:
            raise ValueError('背包鱼类枚举未确认')
        verify_image(reader.meta,layout['address'],ENTITY_IMAGE,verified)
        kind = struct.unpack_from('<i',row,reader.offset(info,'entityType',17,4)-16)[0]
        identity = struct.unpack_from('<i',row,reader.offset(info,'staticId',8,4)-16)[0]
        count = struct.unpack_from('<i',row,reader.offset(info,'count',8,4)-16)[0]
        if identity<=0 or not 0<=count<=100000000:
            raise ValueError('背包数量无效')
        counts[identity] = counts.get(identity,0)+count
        if kind==FISH_ENTITY_TYPE:
            fish_ids.add(identity)
    stable()
    scope = hashlib.sha256(repr((memory.locator.domain,context)).encode()).hexdigest()
    return {'scope':scope,'counts':counts,'fish_ids':fish_ids}
