"""Screen-space map labels and picking; never changes saved world positions."""
from dataclasses import dataclass
import math


def intersects(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def layout_waypoints(points, viewport, active=None, avoid=()):
    """Merge visually coincident captions, retaining every original point index."""
    groups, cells = [], {}
    for index, point in enumerate(points, 1):
        x, y = viewport.screen(point['position'][0], point['position'][2])
        if not (-8 <= x <= viewport.width+8 and -8 <= y <= viewport.height+8):
            continue
        cell = math.floor(x/6), math.floor(y/6)
        nearby = (group for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                  for group in cells.get((cell[0]+dx, cell[1]+dy), ()))
        group = next((g for g in nearby if math.hypot(x-g['anchor'][0], y-g['anchor'][1]) <= 6), None)
        if group is None:
            group = {'anchor': (x, y), 'indexes': [], 'ids': []}
            groups.append(group)
            cells.setdefault(cell, []).append(group)
        group['indexes'].append(index)
        group['ids'].append(str(point['id']))

    occupied = {}

    def cells_for(box):
        return ((x, y) for x in range(math.floor(box[0]/48), math.floor(box[2]/48)+1)
                for y in range(math.floor(box[1]/48), math.floor(box[3]/48)+1))

    def reserve(box):
        for cell in cells_for(box):
            occupied.setdefault(cell, []).append(box)

    for box in avoid:
        reserve(box)
    # Keep the small centered character and its direction arrow unobscured.
    cx, cy = viewport.width/2, viewport.height/2
    reserve((cx-16, cy-18, cx+16, cy+18))
    order = sorted(groups, key=lambda g: (active not in g['indexes'],
                   not (1 in g['indexes'] or len(points) in g['indexes']), g['indexes'][0]))
    for group in order:
        caption = '/'.join(group['ids'])
        if len(caption) > 8:
            caption = f"{group['ids'][0]}+{len(group['ids'])-1}"
        group.update(indexes=tuple(group['indexes']), caption=caption,
                     active=active in group['indexes'], rect=None)
        width, height = max(22, len(caption)*7+12), 20
        x, y = group['anchor']
        for radius in (20, 36, 52, 68):
            for dx, dy in ((1, -1), (-1, -1), (1, 1), (-1, 1), (0, -1), (0, 1), (1, 0), (-1, 0)):
                left, top = x+dx*radius-width/2, y+dy*radius-height/2
                box = (left, top, left+width, top+height)
                if (6 <= box[0] and box[2] <= viewport.width-6 and 6 <= box[1]
                        and box[3] <= viewport.height-6 and not any(
                            intersects(box, b) for cell in cells_for(box) for b in occupied.get(cell, ()))):
                    group['rect'] = box
                    reserve((box[0]-3, box[1]-3, box[2]+3, box[3]+3))
                    break
            if group['rect'] is not None:
                break
    return groups


@dataclass(frozen=True)
class HoverTarget:
    x: float
    y: float
    title: str
    detail: str
    rect: tuple | None = None


class HoverIndex:
    """Only displayed symbols are pickable; pointer motion never scans the scene."""
    def __init__(self, targets=()):
        self.cells = {}
        for order, target in enumerate(targets):
            bounds = target.rect or (target.x-10, target.y-10, target.x+10, target.y+10)
            bounds = (min(bounds[0], target.x-10), min(bounds[1], target.y-10),
                      max(bounds[2], target.x+10), max(bounds[3], target.y+10))
            for x in range(math.floor(bounds[0]/32), math.floor(bounds[2]/32)+1):
                for y in range(math.floor(bounds[1]/32), math.floor(bounds[3]/32)+1):
                    self.cells.setdefault((x, y), []).append((order, target))

    def pick(self, x, y):
        candidates = self.cells.get((math.floor(x/32), math.floor(y/32)), ())
        for _, target in reversed(candidates):
            box = target.rect
            if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return target
        nearby = [(math.hypot(x-t.x, y-t.y), -order, t) for order, t in candidates
                  if math.hypot(x-t.x, y-t.y) <= 10]
        return min(nearby, key=lambda value: value[:2])[2] if nearby else None


def target_indicator(viewport, position):
    x, y = viewport.screen(position[0], position[2])
    if 10 <= x <= viewport.width-10 and 10 <= y <= viewport.height-10:
        return None
    dx, dy = x-viewport.width/2, y-viewport.height/2
    length = math.hypot(dx, dy)
    if not length:
        return None
    ratio = min(max(8, viewport.width/2-24)/abs(dx) if dx else math.inf,
                max(8, viewport.height/2-54)/abs(dy) if dy else math.inf)
    return (viewport.width/2+dx*ratio, viewport.height/2+dy*ratio,
            dx/length, dy/length, length/viewport.scale)


class MapHover:
    def __init__(self, canvas):
        self.canvas, self.index, self.viewport = canvas, HoverIndex(), None
        self.pointer = None

    def update(self, viewport, targets):
        self.viewport, self.index = viewport, HoverIndex(targets)
        if self.pointer is not None:
            self.show(*self.pointer)

    def hide(self):
        self.pointer = None
        self.canvas.delete('map-hover')

    def show(self, x, y):
        self.pointer = (x, y)
        canvas, view = self.canvas, self.viewport
        canvas.delete('map-hover')
        target = self.index.pick(x, y)
        if target is None or view is None:
            return
        text = canvas.create_text(0, 0, text=target.title+'\n'+target.detail,
                                  fill='#e4eddf', font=('Microsoft YaHei UI', -11),
                                  width=max(40, min(260, view.width-32)), anchor='nw', tags='map-hover')
        bounds = canvas.bbox(text)
        width, height = bounds[2]-bounds[0]+18, bounds[3]-bounds[1]+14
        left = max(6, min(x+16, view.width-width-6))
        top = y+18 if y+height+24 <= view.height else max(6, y-height-12)
        top = max(6, min(top, view.height-height-6))
        canvas.coords(text, left+9, top+7)
        box = canvas.create_rectangle(left, top, left+width, top+height,
                                      fill='#12241d', outline='#567563', tags='map-hover')
        canvas.tag_lower(box, text)
        canvas.tag_raise('map-hover')
