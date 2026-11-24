"""Read the main-map build-field boundary; never use door/expanded trigger areas.

The supported managed code uses CraftFieldConfig -> FieldProtocolManager.Min/Max
and MapFieldPointAsset.pos/rot. See docs/M1-house-plots.md for source evidence.
"""
import hashlib
import math
import struct

from utils.core.coordinate_route import valid_position
from utils.core.scene_map import quaternion, rotate

_SOURCE_IMAGE = ('XDTDataAndProtocol', 1667072,
                 '78f58918e9760f65991d625090162c22ab9667e4d3f6868248e8b6c19eaaa0ba')
SOURCE = 'main-map-verified-build-field'


def plot_geometry(position, rotation, limits):
    if not valid_position(position) or len(limits) != 4 or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 500 for v in limits):
        raise ValueError('住宅建造范围无效')
    left, right, front, back = limits
    q = quaternion(rotation)
    points = []
    for x, z in ((-left, -back), (right, -back), (right, front), (-left, front)):
        world = [a+b for a,b in zip(position, rotate((x, 0., z), q))]
        if not valid_position(world):
            raise ValueError('住宅世界坐标无效')
        points.append([world[0], world[2]])
    area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points, points[1:]+points[:1])) / 2
    if not math.isfinite(area) or area <= 1e-6:
        raise ValueError('住宅边界投影退化')
    return points, [sum(p[i] for p in points)/4 for i in range(2)]


class HousePlotReader:
    def __init__(self, entrances):
        self.entrances = entrances
        self.meta = entrances.meta
        self.klass = None
        self.verified_image = None
        self.cache = None
        self.loads = 0

    def limits(self):
        m = self.meta
        if self.klass is None:
            self.klass = self.entrances.find_class('XDTDataAndProtocol', 'CraftFieldConfig',
                                                 'XDTLevelAndEntity.Core.Craft')
        klass = self.klass
        image = m.pointer(klass+0x40)
        raw = m.pointer(image+0x10)
        identity = (m.domain, image, raw)
        if self.verified_image != identity:
            name, length, digest = _SOURCE_IMAGE
            if m.text(m.pointer(image+0x30)) != name or hashlib.sha256(m.data(raw, length)).hexdigest() != digest:
                raise ValueError('住宅边界来源版本未经验证')
            self.verified_image = identity
        vtable = m.pointer(klass+0xd8)
        if m.pointer(vtable) != klass or m.pointer(vtable+16) != m.domain:
            raise ValueError('住宅建造配置运行时变化')
        slots = m.integer(klass+0x5c)
        if not 0 <= slots < 65536:
            raise ValueError('住宅建造配置静态布局无效')
        storage = m.pointer(vtable+0x50+slots*8)
        values = []
        for name in ('_fieldLeft', '_fieldRight', '_fieldFront', '_fieldBack'):
            f = m.field(klass, name)
            if f['code'] != 12 or not f['flags'] & 16 or not 0 <= f['offset'] <= 64:
                raise ValueError('住宅建造配置字段不符')
            values.append(struct.unpack('<f', m.data(storage+f['offset'], 4))[0])
        return identity, storage, tuple(values)

    def snapshot(self):
        self.cache, old = None, self.cache
        h, m = self.entrances, self.meta
        houses = h.snapshot()
        if not isinstance(h.cache, dict) or not all(k in h.cache for k in ('context', 'layout', 'config')):
            raise ValueError('住宅主地图配置布局未完整确认')
        context, layout, config = h.cache['context'], h.cache['layout'], h.cache['config']
        limits = self.limits()
        expected = {row['house_number']: row['field_id'] for row in houses}
        records, signature = {}, []
        for obj in layout[2]:
            info = h.expected(obj, 'MapFieldPointAsset',
                'EcsClient.XDT.Scene.Shared.Data.ServerData.ResourceSpawnConfig')
            nf = h.field(info, 'fieldNo', 8, 4); idf = h.field(info, 'fieldID', 8, 4)
            number = m.integer(obj+nf['offset'])
            if not 1 <= number <= 12:
                continue
            identity = m.integer(obj+idf['offset'])
            if number in records or expected.get(number) != identity:
                raise ValueError('住宅编号与地块身份不一致')
            position = h.vector(obj, 'pos')
            rf = h.field(info, 'rot', 17, 16); rt = m.cls(rf['type'])
            if (rt['name'], rt['namespace'], rt['size']) != ('Quaternion', 'UnityEngine', 32):
                raise ValueError('住宅朝向类型不符')
            raw = m.data(obj+rf['offset'], 16)
            rotation = struct.unpack('<4f', raw)
            polygon, label = plot_geometry(position, rotation, limits[2])
            if (m.integer(obj+nf['offset']) != number or m.integer(obj+idf['offset']) != identity
                    or h.vector(obj, 'pos') != position or m.data(obj+rf['offset'], 16) != raw):
                raise ValueError('住宅边界读取时变化')
            signature.append((number, identity, tuple(position), raw))
            records[number] = dict(house_number=number, field_id=identity, polygon=polygon,
                                   label_position=label, source=SOURCE)
        if set(records) != set(range(1, 13)) or len(set(expected.values())) != 12:
            raise ValueError('12块住宅边界未完整确认')
        if (h.config_points(config) != layout or not h.current_context(context) or self.limits() != limits):
            raise ValueError('住宅边界场景或配置已变化')
        key = (context, layout, limits, tuple(signature))
        if old and old[0] == key:
            self.cache = old
            return old[1]
        result = [records[n] for n in range(1, 13)]
        self.loads += 1
        self.cache = (key, result)
        return result
