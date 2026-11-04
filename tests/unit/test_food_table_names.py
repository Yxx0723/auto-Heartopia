"""Owned food names come from the active table, without a heap sweep."""
from unittest.mock import Mock
import struct
import pytest
from utils.drivers.food_inventory import FoodInventoryReader, FoodNamesPending


def reader():
    r=FoodInventoryReader.__new__(FoodInventoryReader)
    r.names={45420:'温泉蛋'}
    r.name_table=Mock();r.name_table.read.return_value=({45101:'田园沙拉'},('scope',))
    return r


def test_missing_lowest_dish_is_resolved_directly_without_scanning():
    r=reader();r.discover_names({45101},Mock())
    assert r.names[45101]=='田园沙拉'
    r.name_table.read.assert_called_once()


def test_changed_configuration_does_not_keep_previous_names():
    r=reader();r.names[45104]='旧名称';r.names_scope=('old',)
    with pytest.raises(FoodNamesPending):r.discover_names({45101,45104},Mock())
    assert 45104 not in r.names and r.names[45101]=='田园沙拉'


def test_missing_active_table_entry_stays_pending():
    r=reader();r.name_table.read.return_value=({},('scope',))
    with pytest.raises(FoodNamesPending):r.discover_names({45101},Mock())


def test_table_mutation_is_not_swallowed_as_name_pending():
    r=reader();r.name_table.read.side_effect=ValueError('配置表正在变化')
    with pytest.raises(ValueError,match='配置表正在变化'):r.discover_names({45101},Mock())


def table_reader():
    from utils.drivers.item_name_table import ItemNameTable
    t=ItemNameTable.__new__(ItemNameTable);t.fields={'ready':True};t.item_class=44
    t.meta=Mock();t._state=Mock(return_value=(1,2,3,1,0))
    t.meta.cls.return_value={'size':32}
    t.meta.field.side_effect=lambda k,n:dict(code=8 if n=='id' else 14,flags=6,offset=24 if n=='id' else 16)
    t.meta.obj.return_value={'address':44}
    t.meta.integer.return_value=45101
    t.meta.pointer.side_effect=lambda a:a*10
    t.meta.managed_string.side_effect=lambda p:'当前层料理' if p==(100+16)*10 else '基础料理'
    return t


@pytest.mark.parametrize('override',[True,False])
def test_current_level_override_precedes_only_level_zero_base(override):
    t=table_reader();active={45101:100} if override else {}
    t._pairs=Mock(side_effect=[({0:10,1:11,9:99},lambda:True),(active,lambda:True),
                               ({45101:200},lambda:True)])
    names,_=t.read({45101},Mock())
    assert names[45101]==('当前层料理' if override else '基础料理')
    assert all(call.args[0]!=99 for call in t._pairs.call_args_list)


def test_config_change_or_wrong_item_id_prevents_returning_name():
    t=table_reader();t._pairs=Mock(return_value=({1:11,45101:100},lambda:True))
    t._state.side_effect=[(1,2,3,1,0),(1,2,3,2,0)]
    with pytest.raises(ValueError,match='正在变化'):t.read({45101},Mock())
    t=table_reader();t._pairs=Mock(return_value=({1:11,45101:100},lambda:True))
    t.meta.integer.return_value=999
    with pytest.raises(ValueError,match='编号或类型'):t.read({45101},Mock())


def test_generated_1004_field_table_uses_local_bound_without_relaxing_scene_metadata():
    from utils.drivers.item_name_table import ItemNameTable
    from utils.core import thresholds as policy
    t=ItemNameTable.__new__(ItemNameTable);t.klass=0x1000;t.meta=Mock()
    t.meta.integer.return_value=1004;t.meta.pointer.return_value=0x2000
    names={1001:'LevelTableBagitems',1002:'CurLevelId',1003:'languageId'}
    t.meta.text.side_effect=lambda a:names.get(a-0x900000,'other')
    def data(address,length):
        if length==28:
            i=(address-0x2000)//32
            return struct.pack('<QQQi',0x800000+i,0x900000+i,t.klass,i*8)
        raw=bytearray(16);struct.pack_into('<H',raw,8,17)
        raw[10]=21 if address-0x800000==1001 else 8
        return bytes(raw)
    t.meta.data.side_effect=data
    fields=t._fields(Mock())
    assert set(fields)==set(names.values()) and policy.MONO_FIELD_LIMIT==512
    t.meta.cls.assert_not_called()


@pytest.mark.parametrize('bad',['key_type','mutation','count'])
def test_typed_dictionary_rejects_bad_layout_or_mutation(bad):
    from utils.drivers.item_name_table import ItemNameTable
    t=ItemNameTable.__new__(ItemNameTable);m=t.meta=Mock()
    m.obj.return_value=dict(name='Dictionary`2',namespace='System.Collections.Generic',address=1,size=80)
    fields={n:dict(offset=o,code=8,flags=1) for n,o in [('_version',76),('_count',64),('_freeCount',72),('next',20),('key',24)]}
    fields['value']=dict(offset=32,code=18,flags=1)
    if bad=='key_type':fields['key']['code']=14
    m.field.side_effect=lambda k,n:fields[n]
    m.integer.side_effect=lambda a:{1076:1,1064:70000 if bad=='count' else 1,1072:0}[a]
    m.ref.return_value=3000;m.array.return_value=({'element':4},1);m.cls.return_value={'size':40,'address':4}
    raw=bytearray(24);struct.pack_into('<i',raw,4,-1);struct.pack_into('<i',raw,8,45101);struct.pack_into('<Q',raw,16,100)
    m.data.side_effect=[bytes(raw),bytes(24)] if bad=='mutation' else lambda *a:bytes(raw)
    with pytest.raises(ValueError):t._pairs(1000,65536,Mock())
