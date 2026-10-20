"""Read the local ToolSystem through current LevelNode ownership, without writes."""
import struct

from utils.core.tool_durability import tool_status
from utils.drivers.mono_scene import SceneMetadata


class ToolDurabilityReader:
    def __init__(self, read, mono_base, domain):
        self.meta, self.base = SceneMetadata(read, domain), mono_base
        self.world_class = self.discover_world()

    def discover_world(self):
        meta = self.meta
        node, seen, images = meta.pointer(self.base+0x4d3880), set(), []
        while node:
            if node in seen or len(seen) >= 512:
                raise ValueError('工具运行时程序集正在变化')
            seen.add(node)
            image = meta.pointer(meta.pointer(node)+0x68)
            if meta.text(meta.pointer(image+0x30)) == 'XDTBaseService':
                images.append(image)
            node = meta.pointer(node+8)
        if len(images) != 1:
            raise ValueError('工具模块尚未加载')
        image = images[0]
        size, count, buckets = struct.unpack_from('<IIQ', meta.data(image+0x4d0, 40), 24)
        if not 0 < size <= 100000 or not 0 < count <= 100000:
            raise ValueError('工具模块类型缓存无效')
        seeds = [p for p in struct.unpack('<'+'Q'*size, meta.data(buckets, size*8)) if p]
        if not seeds or meta.pointer(seeds[0]+0x40) != image:
            raise ValueError('工具模块类型归属不符')
        return meta.image_class(seeds[0], 'GameWorld', 'XDTGame.Core')

    def expected(self, obj, name, namespace):
        info = self.meta.obj(obj)
        if (info['name'], info['namespace']) != (name, namespace):
            raise ValueError('工具对象类型不符')
        return info

    def field(self, info, name, code, width):
        f = self.meta.field(info['address'], name)
        if f['code'] != code or f['flags'] & 16 or not 16 <= f['offset'] <= info['size']-width:
            raise ValueError('工具字段布局不符')
        return f['offset']

    def stamp(self, dictionary):
        ci = self.expected(dictionary, 'Dictionary`2', 'System.Collections.Generic')
        count = self.meta.integer(dictionary+self.field(ci, '_count', 8, 4))
        version = self.meta.integer(dictionary+self.field(ci, '_version', 8, 4))
        return self.meta.ref(dictionary, '_entries'), count, version

    def target(self, dictionary, tool_id=1):
        meta = self.meta
        stamp = self.stamp(dictionary)
        entries, count, _ = stamp
        if not 0 < count <= 128:
            raise ValueError('工具耐久尚未加载')
        array, capacity = meta.array(entries)
        element = meta.cls(array['element'])
        stride = element['size']-16
        if count > capacity or not 16 <= stride <= 128:
            raise ValueError('工具列表范围无效')
        offsets = [self.field(element, n, code, width)-16 for n, code, width in (
            ('next', 8, 4), ('key', 8, 4), ('value', 18, 8))]
        raw = meta.data(entries+32, count*stride)
        found = []
        for i in range(count):
            start = i*stride
            if struct.unpack_from('<i', raw, start+offsets[0])[0] < -1:
                continue
            if struct.unpack_from('<i', raw, start+offsets[1])[0] == tool_id:
                found.append(struct.unpack_from('<Q', raw, start+offsets[2])[0])
        if len(found) != 1 or not found[0] or self.stamp(dictionary) != stamp:
            raise ValueError('工具耐久列表正在变化')
        return found[0], stamp

    def snapshot(self, held=False):
        meta = self.meta
        if meta.pointer(self.base+0x4d3878) != meta.domain:
            raise ValueError('工具运行时已切换')
        node = meta.static_ref(self.world_class, '_currentLevel')
        self.expected(node, 'LevelNode', 'XDTGame.Core')
        owner, modules = meta.ref(node, '_instance'), meta.ref(node, '_dataModules')
        self.expected(modules, 'SubModules`1', '')
        if not owner or meta.ref(modules, 'gameLevel') != node:
            raise ValueError('工具场景归属不符')
        group = meta.ref(modules, 'group')
        self.expected(group, 'ModuleGroupSorted', 'XDTGame.Core')
        array = meta.ref(group, '_modules')
        members = meta.array_refs(array)
        if len(members) > 512:
            raise ValueError('工具场景模块过多')
        systems = [p for p in members if p and (meta.obj(p)['name'], meta.obj(p)['namespace']) == (
            'ToolSystem', 'XDTGameSystem.GameplaySystem.Tool')]
        if len(systems) != 1:
            raise ValueError('工具系统尚未加载或存在歧义')
        system = systems[0]
        owner_field = '<XDTGame.Core.IModule.Owner>k__BackingField'
        if meta.ref(system, owner_field) != owner:
            raise ValueError('工具系统不属于当前场景实例')
        dictionary = meta.ref(system, '_toolsData')
        si = meta.obj(system)
        hold_field = meta.field(si['address'], '_currentHandHold')
        if hold_field['code'] != 17 or hold_field['flags'] & 16:
            raise ValueError('工具持用字段类型不符')
        hold_type = meta.cls(hold_field['type'])
        if (hold_type['name'], hold_type['namespace']) != (
                'TakenToolComponent', 'EcsClient.XDT.Scene.Shared.Modules.Tools') or hold_type['size'] != 24:
            raise ValueError('工具持用状态类型不符')
        tool_id_offset = self.field(hold_type, 'ToolId', 8, 4)-16
        if not 16 <= hold_field['offset'] <= si['size']-8:
            raise ValueError('工具持用状态范围无效')
        hold = meta.data(system+hold_field['offset'], 8)
        hand_id = struct.unpack_from('<i', hold, tool_id_offset)[0]
        if not 0 <= hand_id <= 100000:
            raise ValueError('手持工具编号无效')
        tool_id = hand_id if held else 1
        tool = None
        stamp = self.stamp(dictionary)
        if held and hand_id == 0:
            result = {'state': 'empty', 'name': '', 'equipped': False}
        else:
            tool, stamp = self.target(dictionary, tool_id)
            ci = self.expected(tool, 'Tool', 'XDTGameSystem.GameplaySystem.Tool')
            raw = meta.data(tool, ci['size'])
            def integer(name):
                return struct.unpack_from('<i', raw, self.field(ci, name, 8, 4))[0]
            name_at = struct.unpack_from('<Q', raw, self.field(ci, 'Name', 14, 8))[0]
            name = meta.managed_string(name_at)
            if integer('Id') != tool_id or (not held and name != '斧镐'):
                raise ValueError('工具身份不符')
            unlocked = raw[self.field(ci, 'unlock', 2, 1)]
            if unlocked not in (0, 1):
                raise ValueError('工具解锁状态无效')
            result = tool_status(name, integer('durability'), integer('maxDurability'), bool(unlocked), hand_id == tool_id)
        if ((tool is not None and (meta.data(tool, ci['size']) != raw
                or self.target(dictionary, tool_id)[0] != tool))
                or meta.data(system+hold_field['offset'], 8) != hold
                or meta.ref(system, '_toolsData') != dictionary or self.stamp(dictionary) != stamp
                or meta.ref(system, owner_field) != owner or meta.ref(node, '_instance') != owner
                or meta.ref(node, '_dataModules') != modules or meta.ref(modules, 'gameLevel') != node
                or meta.ref(modules, 'group') != group or meta.ref(group, '_modules') != array
                or meta.array_refs(array) != members or meta.static_ref(self.world_class, '_currentLevel') != node
                or meta.pointer(self.base+0x4d3878) != meta.domain):
            raise ValueError('工具耐久在读取时发生变化')
        return result
