"""Read active TableData names through typed references, never heap scanning."""
import struct
from utils.drivers.global_minerals import GlobalMineralReader

TABLE_FIELD_LIMIT = 2048  # TableData observed 1004 fields; local metadata bound.
TABLE_ITEM_LIMIT = 65536


class ItemNameTable:
    def __init__(self, meta, base):
        self.meta,self.base=meta,base
        finder=GlobalMineralReader(meta,base,{})
        self.klass=finder.find_class('EcsClient','TableData','')
        self.item_class=finder.find_class('EcsClient','TableBagitem','')
        self.fields=None

    def _fields(self, check):
        # This generated class alone exceeds SceneMetadata's ordinary 512-field
        # bound. Read only our three fields, without relaxing other class limits.
        m=self.meta;k=self.klass
        count=m.integer(k+0x100);table=m.pointer(k+0xa0)
        if not 0<count<=TABLE_FIELD_LIMIT:
            raise ValueError('物品名称配置字段数量无效')
        found={}
        for i in range(count):
            check()
            typ,name,owner,offset=struct.unpack('<QQQi',m.data(table+i*32,28))
            name=m.text(name)
            if name not in ('LevelTableBagitems','CurLevelId','languageId'):
                continue
            details=m.data(typ,16);code=details[10];flags=struct.unpack_from('<H',details,8)[0]
            if owner!=k or not flags&16 or code!=(21 if name=='LevelTableBagitems' else 8) \
                    or not 0<=offset<65536 or name in found:
                raise ValueError('物品名称配置字段类型不符')
            found[name]=offset
        if len(found)!=3:
            raise ValueError('物品名称配置缺少当前层或语言入口')
        if m.integer(k+0x100)!=count or m.pointer(k+0xa0)!=table:
            raise ValueError('物品名称配置元数据正在变化')
        return found

    def _state(self):
        m=self.meta;k=self.klass
        if m.pointer(self.base+0x4d3878)!=m.domain:
            raise ValueError('物品名称配置运行时已切换')
        vt=m.pointer(k+0xd8);slots=m.integer(k+0x5c)
        if m.pointer(vt)!=k or m.pointer(vt+16)!=m.domain or not 0<=slots<4096:
            raise ValueError('物品名称配置静态入口不符')
        storage=m.pointer(vt+0x50+slots*8)
        return (vt,storage,m.pointer(storage+self.fields['LevelTableBagitems']),
                m.integer(storage+self.fields['CurLevelId']),m.integer(storage+self.fields['languageId']))

    def _pairs(self, obj, limit, check):
        m=self.meta;ci=m.obj(obj)
        if (ci['name'],ci['namespace'])!=('Dictionary`2','System.Collections.Generic'):
            raise ValueError('物品名称配置不是字典')
        fields=[m.field(ci['address'],n) for n in ('_version','_count','_freeCount')]
        if any(f['code']!=8 or f['flags']&16 or not 16<=f['offset']<=ci['size']-4 for f in fields):
            raise ValueError('物品名称字典计数字段无效')
        def stamp():
            return (*[m.integer(obj+f['offset']) for f in fields],m.ref(obj,'_entries'))
        before=stamp();_,count,free,array=before
        if not 0<=free<=count<=TABLE_ITEM_LIMIT or count-free>limit:
            raise ValueError('物品名称字典数量无效')
        if count==free:
            return {},lambda:stamp()==before
        ai,capacity=m.array(array,TABLE_ITEM_LIMIT);entry=m.cls(ai['element']);stride=entry['size']-16
        key,value,nxt=[m.field(entry['address'],n) for n in ('key','value','next')]
        if count>capacity or not 16<=stride<=128 or key['code']!=8 \
                or value['code'] not in (18,21) or nxt['code']!=8 \
                or any(f['flags']&16 for f in (key,value,nxt)) \
                or not all(16<=f['offset']<=entry['size']-width for f,width in ((key,4),(value,8),(nxt,4))):
            raise ValueError('物品名称字典元素布局不符')
        raw=m.data(array+32,count*stride);rows={}
        for i in range(count):
            if i%256==0:check()
            at=i*stride
            if struct.unpack_from('<i',raw,at+nxt['offset']-16)[0]<-1:
                continue
            identity=struct.unpack_from('<i',raw,at+key['offset']-16)[0]
            pointer=struct.unpack_from('<Q',raw,at+value['offset']-16)[0]
            if identity in rows or not pointer:
                raise ValueError('物品名称配置有重复或空引用')
            rows[identity]=pointer
        def stable():
            return stamp()==before and m.data(array+32,len(raw))==raw
        if len(rows)!=count-free or not stable():
            raise ValueError('物品名称字典正在变化')
        return rows,stable

    def read(self, wanted, check):
        check()
        if self.fields is None:self.fields=self._fields(check)
        before=self._state()
        levels,outer_stable=self._pairs(before[2],32,check)
        selected=levels.get(before[3])
        if selected is None:
            raise ValueError('当前配置层的物品名称表尚未加载')
        entries,inner_stable=self._pairs(selected,TABLE_ITEM_LIMIT,check)
        witnesses=[inner_stable]
        # The active level contains overrides; ordinary food names are in the
        # level-0 base table. Never borrow from an unrelated level dictionary.
        if before[3]!=0 and any(item not in entries for item in wanted) and 0 in levels:
            base,base_stable=self._pairs(levels[0],TABLE_ITEM_LIMIT,check)
            entries={**base,**entries}
            witnesses.append(base_stable)
        m=self.meta;result={}
        ci=m.cls(self.item_class)
        identity,name=[m.field(self.item_class,n) for n in ('id','name')]
        if identity['code']!=8 or name['code']!=14 or any(f['flags']&16 for f in (identity,name)) \
                or not 16<=identity['offset']<=ci['size']-4 or not 16<=name['offset']<=ci['size']-8:
            raise ValueError('物品名称记录布局不符')
        for item_id in wanted:
            check();obj=entries.get(item_id)
            if obj is None:continue
            if m.obj(obj)['address']!=self.item_class or m.integer(obj+identity['offset'])!=item_id:
                raise ValueError('物品名称编号或类型不符')
            pointer=m.pointer(obj+name['offset']);text=m.managed_string(pointer)
            if not 1<=len(text)<=32 or any(ord(c)<32 for c in text):
                raise ValueError('物品名称文本无效')
            if m.pointer(obj+name['offset'])!=pointer or m.integer(obj+identity['offset'])!=item_id:
                raise ValueError('物品名称记录正在变化')
            result[item_id]=text
        if self._state()!=before or not outer_stable() or not all(test() for test in witnesses):
            raise ValueError('物品名称配置正在变化')
        return result,before
