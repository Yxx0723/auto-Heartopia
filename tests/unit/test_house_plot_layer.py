"""Rendering boundaries must never retain stale houses or duplicate numbers."""
from copy import deepcopy
from utils.drivers.house_plots import plot_geometry, SOURCE
from utils.ui.house_plot_layer import HousePlotLayer, PLOT_COLOR, TAG
from utils.ui.map_draw_cache import Viewport
from utils.ui.native_coordinate_monitor import SCENE_CATEGORIES


class Canvas:
    def __init__(self): self.items={}; self.serial=0
    def delete(self, tag): self.items={k:v for k,v in self.items.items() if tag not in v[1].get('tags',())}
    def create(self,*coords,**opts):
        self.serial+=1;self.items[self.serial]=(coords,opts);return self.serial
    create_polygon=create_text=create
    def coords(self, item, *coords):
        self.items[item] = (coords, self.items[item][1])

    def tag_raise(self,tag): pass


def scene(context=(1,'startown')):
    records=[]
    for n in range(1,13):
        polygon,label=plot_geometry([n*40.,0,0],[0,0,0,1],(12,12,9,21))
        records.append(dict(house_number=n,field_id=13-n,polygon=polygon,label_position=label,source=SOURCE))
    return dict(state='ready',house_plot_state='ready',house_plots=records,cache_context=context)


def test_exactly_twelve_boundaries_and_unique_interior_numbers():
    c=Canvas();layer=HousePlotLayer(c);s=scene();view=Viewport(800,600,0,0,2.)
    layer.draw(s,view)
    assert len(c.items)==24
    text=[o['text'] for _,o in c.items.values() if 'text' in o]
    assert sorted(map(int,text))==list(range(1,13))
    for _,o in c.items.values():
        if 'outline' in o: assert o['fill']=='' and o['outline']==PLOT_COLOR
    layer.draw(deepcopy(s),view)
    assert layer.redraws==1 and c.serial==24
    assert 'house_plot' in SCENE_CATEGORIES


def test_pan_zoom_resize_reprojects_polygons_and_numbers_without_duplicates():
    c=Canvas();layer=HousePlotLayer(c);s=scene()
    for view in (Viewport(800,600,0,0,2.),Viewport(900,700,40,20,5.),Viewport(600,400,-10,-20,.5)):
        layer.draw(s,view)
        assert len(c.items)==24
        first_poly,first_text=list(c.items.values())[:2]
        assert first_poly[0][:2]==view.screen(*s['house_plots'][0]['polygon'][0])
        assert first_text[0]==view.screen(*s['house_plots'][0]['label_position'])
    assert c.serial == 24


def test_scene_failure_switch_filter_and_reconnect_leave_no_old_items():
    c=Canvas();layer=HousePlotLayer(c);view=Viewport(800,600,0,0,2.);s=scene()
    layer.draw(s,view);layer.draw({'state':'waiting'},view);assert not c.items
    layer.draw(s,view);layer.draw(s,view,False);assert not c.items
    layer.draw(scene((2,'another-scene')),view);assert len(c.items)==24
    layer.draw(dict(state='ready',cache_context=(2,'craft'),house_plot_state='unavailable'),view)
    assert not c.items


def test_incomplete_duplicate_bowtie_and_outside_labels_fail_closed():
    view=Viewport(800,600,0,0,2.)
    for fault in ('missing','number','identity','bowtie','label','source'):
        c=Canvas();layer=HousePlotLayer(c);s=scene();layer.draw(s,view)
        invalid=deepcopy(s)
        if fault=='missing':invalid['house_plots'].pop()
        if fault=='number':invalid['house_plots'][0]['house_number']=2
        if fault=='identity':invalid['house_plots'][0]['field_id']=11
        if fault=='bowtie':invalid['house_plots'][0]['polygon'][1:3]=invalid['house_plots'][0]['polygon'][2:0:-1]
        if fault=='label':invalid['house_plots'][0]['label_position']=[10000,10000]
        if fault=='source':invalid['house_plots'][0]['source']='forecourt-trigger'
        layer.draw(invalid,view);assert not c.items
