"""Owned native UI layout, read only, for the explicitly verified game build.

Rect/anchoredPosition offsets follow this build's actual Unity accessors. No
functions are invoked remotely. A layout observation is not a click permit.
"""
import math
import struct

from utils.drivers.il2cpp_scene import NativeSceneMetadata
from utils.core.native_scene_versions import native_scene_layout
from utils.core.ui_projection import transform_point
from utils.core.map_targeting import MapLayoutUpdating


class NativeUiLayout:
    def __init__(self,memory,meta):
        # The reader checks the loaded files before resolving this exact build.
        layout=native_scene_layout(memory.profile)
        self.meta=meta
        self.native=NativeSceneMetadata(memory._read,memory._module('GameAssembly.dll')[0],layout)

    @staticmethod
    def _expect(info,name,namespace):
        if (info.get('name'),info.get('namespace'))!=(name,namespace):
            raise ValueError('界面对象类型与布局不符')

    def resolve(self,obj,name='RectTransform',namespace='UnityEngine',kind=1,*,native_namespace=None):
        m,n=self.meta,self.native
        info=m.obj(obj);self._expect(info,name,namespace)
        field=m.field(info['address'],'_handle_')
        if field['code']!=11 or field['flags']&16 or not 16<=field['offset']<=info['size']-8:
            raise ValueError('界面句柄字段未确认')
        raw_handle=m.data(obj+field['offset'],8);handle=struct.unpack('<Q',raw_handle)[0]
        if not handle:
            raise ValueError('界面句柄已失效')
        translator=n.class_at(n.layout.object_translator,'ObjectTranslator','MonoGame.Core')['address']
        host=n.static_ref(translator,'_hostObjectMapUnitySide')
        self._expect(n.obj(host),'HostObjectMapUnitySide','MonoGame.Core')
        refs=n.ref(host,'_objectRefs');element=n.cls(n.obj(refs)['element'])
        if (element['name'],element['namespace'],element['size'],element['value_type']) \
                !=('ObjectItem','',32,True):
            raise ValueError('界面句柄目录布局未确认')
        count=struct.unpack('<Q',n.data(refs+24,8))[0]
        index=handle&0xffffffff
        if not 0<count<=1048576 or index>=count:
            raise ValueError('界面句柄超出目录范围')
        slot=refs+32+index*16;raw=n.data(slot,16)
        target,_,type_id,actual_kind=struct.unpack('<QiHH',raw)
        if type_id!=handle>>48 or actual_kind!=kind or not target:
            raise ValueError('界面句柄类型或存储种类已改变')
        self._expect(n.obj(target),name,native_namespace if native_namespace is not None else namespace)
        if m.data(obj+field['offset'],8)!=raw_handle or n.data(slot,16)!=raw \
                or n.ref(host,'_objectRefs')!=refs \
                or n.static_ref(translator,'_hostObjectMapUnitySide')!=host \
                or struct.unpack('<Q',n.data(refs+24,8))[0]!=count:
            raise ValueError('界面句柄目录正在变化')
        return target

    def rect(self,obj):
        target=self.resolve(obj);result=self.native_rect(target)
        if self.resolve(obj)!=target:
            raise ValueError('地图矩形对象归属正在变化')
        return result

    def native_rect(self,target):
        n=self.native;self._expect(n.obj(target),'RectTransform','UnityEngine')
        info=n.obj(target);f=n.field(info['address'],'m_CachedPtr')
        if not 16<=f['offset']<=info['size']-8:
            raise ValueError('原生界面引用字段超出范围')
        pointer=n.pointer(target+f['offset'])
        raw=n.data(pointer+0xb8,64)
        # get_rect updates its cached rectangle when this dirty byte is set.
        # A read-only observer must wait, rather than calling that updater.
        if raw[0x39]:
            raise MapLayoutUpdating('地图矩形布局正在更新')
        rect=struct.unpack_from('<4f',raw)
        anchored=struct.unpack_from('<2f',raw,0x20)
        if not all(math.isfinite(x) for x in (*rect,*anchored)) or min(rect[2:])<=0:
            raise ValueError('地图矩形或平移数值无效')
        if n.pointer(target+f['offset'])!=pointer:
            raise ValueError('地图矩形或其对象归属正在变化')
        if n.data(pointer+0xb8,64)!=raw:
            raise MapLayoutUpdating('地图矩形布局正在更新')
        return {'rect':rect,'anchored_position':anchored,'read_only':True}

    def transforms(self,obj):
        """Owned TransformAccess local TRS chain, without invoking Unity getters."""
        m=self.meta;name=m.obj(obj)['name']
        if name not in ('Transform','RectTransform'):
            raise ValueError('界面变换对象类型未确认')
        target=self.resolve(obj,name);result=self.native_transforms(target)
        if self.resolve(obj,name)!=target:
            raise ValueError('界面变换对象归属正在变化')
        return result

    def native_transforms(self,target):
        n=self.native;info=n.obj(target)
        if info['name'] not in ('Transform','RectTransform') or info['namespace']!='UnityEngine':
            raise ValueError('原生界面变换类型未确认')
        field=n.field(info['address'],'m_CachedPtr')
        if not 16<=field['offset']<=info['size']-8:
            raise ValueError('原生界面引用字段超出范围')
        pointer=n.pointer(target+field['offset'])
        access=n.data(pointer+0x38,12);hier,index=struct.unpack('<Qi',access)
        tables=n.data(hier+0x18,16);data,parents=struct.unpack('<QQ',tables)
        seen=set();records=[];checks=[]
        # These are bounded-read engineering guards, not a claimed Unity
        # allocation length or game movement capability. Every read can fail.
        while index!=-1:
            if not 0<=index<65536 or index in seen or len(seen)>=64:
                raise ValueError('界面父级变换链无效或过长')
            seen.add(index);raw=n.data(data+index*48,48);parent=n.data(parents+index*4,4)
            values=struct.unpack('<12f',raw)
            records.append((values[:3],values[4:8],values[8:11]))
            checks.append((data+index*48,raw));checks.append((parents+index*4,parent))
            index=struct.unpack('<i',parent)[0]
        chain=tuple(records);transform_point((0.,0.,0.),chain)
        if n.data(pointer+0x38,12)!=access or n.data(hier+0x18,16)!=tables \
                or n.pointer(target+field['offset'])!=pointer:
            raise ValueError('界面变换链正在更新')
        if any(n.data(at,len(raw))!=raw for at,raw in checks):
            raise MapLayoutUpdating('界面变换数值正在更新')
        return chain

    def button_geometry(self,component,observation=None):
        """Reach a button's real rectangle through its owned target Graphic."""
        m,n=self.meta,self.native;info=m.obj(component)
        if info['name'] not in ('CustomButton','CustomSwapButton','CustomToggleButton'):
            raise ValueError('地图按钮组件类型未确认')
        wrapper=m.ref(component,'unityComponent');wi=m.obj(wrapper)
        if (wi['name'],wi['namespace'])!=('Button','UnityEngine.UI'):
            raise ValueError('地图按钮原生桥接类型未确认')
        target=self.resolve(wrapper,'Button','UnityEngine.UI')
        if observation is not None:
            observation.update(button_wrapper=wrapper, button_native=target,
                               button_type=f"{wi['namespace']}.{wi['name']}")
        graphic=n.ref(target,'m_TargetGraphic')
        if observation is not None:observation['target_graphic']=graphic
        rect=n.ref(graphic,'m_RectTransform')
        if observation is not None:observation['rect_transform']=rect
        for name in ('m_Interactable','m_GroupsAllowInteraction'):
            f=n.field(n.obj(target)['address'],name,2)
            enabled=n.data(target+f['offset'],1)==b'\x01'
            if observation is not None:observation[name]=enabled
            if not enabled:
                raise ValueError('地图按钮当前不可交互')
        geometry=self.native_rect(rect)
        if observation is not None:observation['local_rect']=geometry['rect']
        chain=self.native_transforms(rect)
        if m.ref(component,'unityComponent')!=wrapper \
                or self.resolve(wrapper,'Button','UnityEngine.UI')!=target \
                or n.ref(target,'m_TargetGraphic')!=graphic or n.ref(graphic,'m_RectTransform')!=rect:
            raise ValueError('地图按钮对象归属正在变化')
        if observation is not None:
            observation.update(button_binding_valid=True, transform_chain=chain)
        return geometry['rect'],chain

    def active(self,obj):
        n=self.native;target=self.resolve(obj,'GameObject');info=n.obj(target)
        field=n.field(info['address'],'m_CachedPtr')
        if not 16<=field['offset']<=info['size']-8:
            raise ValueError('原生界面引用字段超出范围')
        pointer=n.pointer(target+field['offset'])
        raw=n.data(pointer+0x56,2)
        # activeInHierarchy==255 means Unity has not refreshed that cache yet.
        if raw[0] not in (0,1) or raw[1] not in (0,1):
            raise ValueError('界面显示状态尚未更新')
        if n.data(pointer+0x56,2)!=raw or n.pointer(target+field['offset'])!=pointer \
                or self.resolve(obj,'GameObject')!=target:
            raise ValueError('界面显示状态正在变化')
        return raw==b'\x01\x01'
