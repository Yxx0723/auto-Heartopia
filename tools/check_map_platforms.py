import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk
from utils.ui.map_draw_cache import MapDrawCache, Viewport
from utils.ui.map_surface_reference import draw_wooden_platforms, WOODEN_PLATFORM_FOOTPRINTS
from utils.core.coordinate_navigation import inside
assert any(inside((61.3119,-191.3418), list(zip(p[::2],p[1::2]))) for p in WOODEN_PLATFORM_FOOTPRINTS)
r=tk.Tk();r.withdraw();c=tk.Canvas(r,width=800,height=600)
cache=MapDrawCache(c)
for scale in (3.,1.,4.,4.):
 cache.begin(Viewport(800,600,61.,-191.,scale))
 draw_wooden_platforms(cache,scale);cache.finish()
 assert len(c.find_withtag('wooden-platform'))==10
 assert len([i for i in c.find_withtag('wooden-platform-label') if c.itemcget(i,'state')!='hidden'])==(10 if scale>=2.5 else 0)
assert c.itemcget(c.find_withtag('wooden-platform')[0],'fill')=='#B88752'
r.destroy();print('PASS: standing point covered; 10 footprints persist across redraw/zoom; labels hide at low zoom')
