"""Independent display-only house layer, with no navigation geometry or history."""
import math
from utils.drivers.house_plots import SOURCE

PLOT_COLOR = '#F2DFA2'
TAG = 'house-plots'


def validated_plots(scene):
    if scene.get('state') != 'ready' or scene.get('house_plot_state') != 'ready' or not scene.get('cache_context'):
        return ()
    rows = scene.get('house_plots', ())
    if not isinstance(rows, (list, tuple)) or any(not isinstance(p, dict) for p in rows):
        return ()
    if len(rows) != 12 or {p.get('house_number') for p in rows} != set(range(1, 13)) \
            or len({p.get('field_id') for p in rows}) != 12:
        return ()
    for row in rows:
        if type(row.get('field_id')) is not int or row['field_id'] <= 0 or type(row.get('house_number')) is not int:
            return ()
        polygon, label = row.get('polygon', ()), row.get('label_position', ())
        if not isinstance(polygon, (tuple, list)) or not isinstance(label, (tuple, list)):
            return ()
        if row.get('source') != SOURCE or len(polygon) != 4 or len(label) != 2:
            return ()
        if any(not isinstance(p, (list, tuple)) or len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p)
               for p in (*polygon, label)):
            return ()
        turns = [(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])
                 for a,b,c in zip(polygon, polygon[1:]+polygon[:1], polygon[2:]+polygon[:2])]
        if any(v <= 1e-8 for v in turns):
            return ()
        # Labels must be strictly inside this convex boundary.
        if any((b[0]-a[0])*(label[1]-a[1])-(b[1]-a[1])*(label[0]-a[0]) <= 0
               for a,b in zip(polygon, polygon[1:]+polygon[:1])):
            return ()
    return rows


class HousePlotLayer:
    def __init__(self, canvas):
        self.canvas, self.signature = canvas, None
        self.viewport = None
        self.items = []
        self.redraws = 0

    def draw(self, scene, viewport, enabled=True):
        rows = validated_plots(scene) if enabled else ()
        signature = (repr(scene.get('cache_context')),
                     tuple((r['house_number'], r['field_id'], tuple(map(tuple, r['polygon'])),
                            tuple(r['label_position'])) for r in rows))
        if signature == self.signature:
            if viewport != self.viewport:
                for row, (boundary, label) in zip(rows, self.items):
                    self.canvas.coords(boundary, *(v for p in row['polygon'] for v in viewport.screen(*p)))
                    self.canvas.coords(label, *viewport.screen(*row['label_position']))
                self.viewport = viewport
            return False
        self.canvas.delete(TAG)
        self.signature = signature
        self.viewport = viewport
        self.redraws += 1
        self.items = []
        for row in rows:
            coords = [v for p in row['polygon'] for v in viewport.screen(*p)]
            boundary = self.canvas.create_polygon(*coords, fill='', outline=PLOT_COLOR, width=2,
                                                  tags=(TAG, 'house-plot-boundary'))
            label = self.canvas.create_text(*viewport.screen(*row['label_position']), text=str(row['house_number']),
                                            fill='#53452D', font=('Microsoft YaHei UI', 11, 'bold'),
                                            tags=(TAG, 'house-plot-number'))
            self.items.append((boundary, label))
        return bool(self.items)
