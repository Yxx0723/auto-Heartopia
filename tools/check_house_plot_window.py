"""Render current read-only verified plot data in the real Windows map window.

Run in the interactive desktop session. Captures only this window's map canvas.
No game input is sent; the preview camera is centred on the twelve plots.
"""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import Mock
import tkinter as tk
if __name__ == '__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import ImageGrab
from utils.ui.native_coordinate_monitor import CoordinateWindow, MAP_PIXELS_PER_UNIT
from utils.ui.house_plot_layer import TAG


def main():
    rootdir=Path(__file__).resolve().parents[1]
    evidence=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    output=Path(sys.argv[2])
    assert evidence['state']=='passed'
    monitor=Mock();monitor.status.return_value={'state':'waiting','message':'M1只读验收预览'}
    with TemporaryDirectory() as tmp:
     root=tk.Tk();root.withdraw()
     window=CoordinateWindow(root,monitor,rootdir/'webui/coordinate-monitor',point_path=Path(tmp)/'points.json')
     try:
      assert 'house_plot' in window.scene_categories
      assert window.scene_filter_buttons['house_plot'].cget('text').endswith('住宅地皮')
      root.title('M1 住宅地皮 · 只读实机数据验收');root.geometry('1400x900+10+10');root.deiconify()
      root.update();window.toggle_map_size();root.update_idletasks()
      rows=evidence['plots'];points=[p for r in rows for p in r['polygon']]
      cx=(min(p[0] for p in points)+max(p[0] for p in points))/2
      cz=(min(p[1] for p in points)+max(p[1] for p in points))/2
      c=window.canvas
      scale=min((c.winfo_width()-80)/(max(p[0] for p in points)-min(p[0] for p in points)),
                (c.winfo_height()-80)/(max(p[1] for p in points)-min(p[1] for p in points)))
      window.zoom=scale/MAP_PIXELS_PER_UNIT;window.map_view='nearby'
      window.view.position=(cx,0,cz);window.view.trail=[]
      scene=dict(state='ready',house_plot_state='ready',house_plots=rows,objects=[],colliders=[],
                 cache_context=('verified-live','startown'))
      window.scene_state=scene;window.draw();root.update_idletasks()
      print('map-check',c.winfo_width(),c.winfo_height(),c.winfo_ismapped(),window.scene_objects_var.get(),window.scene_categories,window.house_plot_layer.redraws,len(c.find_withtag('house-plot-boundary')),flush=True)
      assert len(c.find_withtag('house-plot-boundary'))==12
      assert len(c.find_withtag('house-plot-number'))==12
      for item in c.find_withtag('house-plot-number'):
       x,y=c.coords(item);assert 0<x<c.winfo_width() and 0<y<c.winfo_height()
      time.sleep(.4)
      bbox=(c.winfo_rootx(),c.winfo_rooty(),c.winfo_rootx()+c.winfo_width(),c.winfo_rooty()+c.winfo_height())
      ImageGrab.grab(bbox=bbox).save(output)
      window.toggle_scene_category('house_plot');assert not c.find_withtag(TAG)
      window.toggle_scene_category('house_plot');assert len(c.find_withtag('house-plot-boundary'))==12
      window.scene_state={'state':'waiting'};window.draw();assert not c.find_withtag(TAG)
      print('PASS: real CoordinateWindow; default filter on; 12 visible labels; toggle and scene clear; screenshot '+str(output))
     finally:window.close()


if __name__ == '__main__':
    main()
