"""Windows read-only cross-check against live field geometry and real Tk drawing.

No game input, remote calls, navigation snapshot or game memory writes.
"""
import argparse
import json
import math
from pathlib import Path
import statistics
import struct
import sys
import time

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.drivers.coordinate_process import find_game_processes
from utils.drivers.house_entrances import HouseEntranceReader
from utils.drivers.house_plots import HousePlotReader, SOURCE
from utils.drivers.mono_scene import MemorySceneMap
from utils.core.scene_map import box_geometry
from utils.ui.house_plot_layer import HousePlotLayer
from utils.ui.map_draw_cache import Viewport


def inline(meta, obj, name, count):
    f=meta.field(meta.obj(obj)['address'],name)
    if f['code']!=17 or f['flags']&16:raise ValueError('Inline field type mismatch')
    return struct.unpack('<'+'f'*count,meta.data(obj+f['offset'],count*4))


def check(profile, output):
    import tkinter as tk
    r=MemorySceneMap(find_game_processes()[0],profile)
    try:
        m=r.meta;h=HouseEntranceReader(m,r.memory.base,{});reader=HousePlotReader(h)
        start=time.perf_counter();plots=reader.snapshot();cold=time.perf_counter()-start
        timings=[]
        for _ in range(20):
            start=time.perf_counter();assert reader.snapshot() is plots;timings.append(time.perf_counter()-start)
        houses={p['field_id']:p for p in plots}
        local=r.local_hierarchy(r.memory.snapshot());klass=m.obj(local)['address']
        registry=m.static_ref(klass,'ActiveHierarchies');entries,stamp=m.dictionary(registry,with_stamp=True)
        config_assets=set(h.cache['layout'][2]);matches={};build_bounds=[]
        for entry in entries:
            obj=m.ref(entry,'_hierarchyObject')
            if not obj:continue
            oi=m.obj(obj)
            if oi['name']=='StaticFieldTarget':
                asset=m.ref(obj,'FieldAsset');ai=m.obj(asset)
                number=m.integer(asset+m.field(ai['address'],'fieldNo')['offset'])
                if not 1<=number<=12:continue
                assert asset in config_assets
                field_id=m.integer(asset+m.field(ai['address'],'fieldID')['offset'])
                polygon=m.ref(obj,'_fieldPolygon');geom=m.ref(polygon,'geometry')
                gf=m.field(m.obj(geom)['address'],'Struct');gi=m.cls(gf['type'])
                assert (gi['name'],gi['namespace'],gi['size'])==('Rect3D','XDTGame.Core.SceneQuery',40)
                cx,cz,sx,sz,above,below=struct.unpack('<6f',m.data(geom+gf['offset'],24))
                hierarchy=m.ref(obj,'_hierarchy');assert not m.ref(hierarchy,'_parent')
                shape=box_geometry(inline(m,hierarchy,'_position',3),inline(m,hierarchy,'_rotation',4),
                    [cx,0,cz],[sx,0,sz],allow_planar=True)['polygon']
                actual=houses[field_id]
                error=max(min(math.dist(a,b) for b in shape) for a in actual['polygon'])
                assert error<1e-4 and number==actual['house_number'] and number not in matches
                matches[number]=dict(field_id=field_id,world_corner_max_error=error,
                                     local_center=[cx,cz],local_size=[sx,sz])
            elif oi['name']=='LevelEntityComponent':
                entity=m.ref(obj,'<entity>k__BackingField')
                for component in m.dictionary(m.ref(entity,'_components'),max_items=128):
                    ci=m.obj(component)
                    if ci['name']!='HomelandComponent':continue
                    f=m.field(ci['address'],'_componentData');typ=m.cls(f['type'])
                    identity=m.integer(component+f['offset']+m.field(typ['address'],'fieldID')['offset']-16)
                    if identity not in houses:continue
                    field=m.ref(component,'_fieldComponent')
                    centerx,cy,centerz,ex,ey,ez=inline(m,field,'<LocalBounds>k__BackingField',6)
                    limits=reader.limits()[2];left,right,front,back=limits
                    expected=((right-left)/2,(front-back)/2,(right+left)/2,(front+back)/2)
                    assert max(abs(a-b) for a,b in zip((centerx,centerz,ex,ez),expected))<1e-4
                    build_bounds.append(dict(house_number=houses[identity]['house_number'],field_id=identity,
                                             local_xz_bounds=[centerx-ex,centerz-ez,centerx+ex,centerz+ez]))
        assert set(matches)==set(range(1,13))
        assert m.dictionary_stamp(registry)==stamp and h.current_context(h.cache['context'])
        scene=dict(state='ready',house_plot_state='ready',house_plots=plots,cache_context=('live-verified','startown'))
        root=tk.Tk();root.withdraw();canvas=tk.Canvas(root);layer=HousePlotLayer(canvas)
        try:
            for viewport in (Viewport(1000,700,0,0,2),Viewport(600,400,40,-20,4),Viewport(1400,850,-30,40,1)):
                layer.draw(scene,viewport);root.update_idletasks()
                assert len(canvas.find_withtag('house-plot-boundary'))==12
                ids=canvas.find_withtag('house-plot-number')
                assert sorted(int(canvas.itemcget(i,'text')) for i in ids)==list(range(1,13))
                for item,row in zip(ids,plots):
                    assert tuple(canvas.coords(item))==viewport.screen(*row['label_position'])
            layer.draw({'state':'waiting'},viewport);assert not canvas.find_withtag('house-plots')
            layer.draw(scene,viewport,False);assert not canvas.find_withtag('house-plots')
        finally:root.destroy()
        publication_start=time.perf_counter();publication=r.snapshot()
        assert publication.get('house_plot_state')=='ready' and len(publication.get('house_plots',()))==12
        publication_ms=(time.perf_counter()-publication_start)*1000
        result=dict(publication=dict(plot_count=12,state=publication['state'],world_state=publication['world_state'],elapsed_ms=publication_ms),state='passed',source=SOURCE,read_only=True,input_sent=False,plot_count=len(plots),
            plots=plots,game_assembly_sha256=r.memory.profile['game_assembly_sha256'],
            static_field_crosscheck=matches,loaded_build_bounds=build_bounds,
            timings_ms=dict(first=cold*1000,warm_median=statistics.median(timings)*1000,warm_max=max(timings)*1000),
            windows_tk=dict(boundaries=12,unique_numbers=12,zoom_pan_resize=True,scene_clear=True,filter_clear=True))
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('plots','static_field_crosscheck')},ensure_ascii=False))
    finally:r.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path,default=Path('webui/coordinate-monitor/profile.json'))
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    check(args.profile,args.output)
