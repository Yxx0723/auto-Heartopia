"""Lightweight cartographic overlays in native Tk, with no game-side effects."""
import math
from functools import lru_cache

# Palette is owned by the central theme module. Re-exporting the name keeps the
# existing `from utils.ui.map_presentation import MAP_STYLE` imports intact.
from utils.ui.theme import MAP_STYLE


def draw_grid(canvas, viewport, step):
    """Grid stays beneath the retained geometry and is bounded by viewport size."""
    left, right, low, high = viewport.bounds()
    for axis, start, end in ((0, left, right), (1, low, high)):
        for index in range(math.ceil(start / step), math.floor(end / step) + 1):
            value = index * step
            major = index % 5 == 0
            color = MAP_STYLE['grid_major' if major else 'grid']
            x, z = viewport.screen(value, value)
            if axis == 0:
                canvas.create_line(x, 0, x, viewport.height, fill=color, tags='map-grid')
                if 32 < x < viewport.width - 70:
                    canvas.create_text(x + 4, viewport.height - 40, text=f'{value:g}',
                                       fill=MAP_STYLE['muted'], font=('Consolas', -9),
                                       anchor='w', tags='map-grid')
            else:
                canvas.create_line(0, z, viewport.width, z, fill=color, tags='map-grid')
                if 62 < z < viewport.height - 50:
                    canvas.create_text(9, z - 6, text=f'{value:g}', fill=MAP_STYLE['muted'],
                                       font=('Consolas', -9), anchor='w', tags='map-grid')
    canvas.tag_lower('map-grid')


def chrome_bounds(viewport, step):
    """Reserve only the remaining wide-map resource legend."""
    w, h = viewport.width, viewport.height
    return [(w-327, h-35, w-12, h-8)] if w >= 570 else []


def draw_chrome(canvas, viewport, step, route_view=False, reference_roads=True, heading_known=False, resource_view=False):
    """Draw the wide-map legend; mode badge, compass and scale bar are removed."""
    w, h = viewport.width, viewport.height
    if w < 570:
        return
    panel, muted = MAP_STYLE['panel'], MAP_STYLE['muted']
    z = h - 19
    labels = [('角色', MAP_STYLE['player']), ('点位', MAP_STYLE['route']),
              ('寻路', MAP_STYLE['navigation']), ('水域', MAP_STYLE['shore']),
              ('道路参考', MAP_STYLE['road'])]
    if not reference_roads:
        labels.pop()
    x = w - 318
    canvas.create_rectangle(x-9, h-35, w-12, h-8, fill=panel, outline='', tags='map-chrome')
    for label, color in labels:
        canvas.create_line(x, z, x+8, z, fill=color, width=3, tags='map-chrome')
        canvas.create_text(x+13, z, text=label, fill=muted,
                           font=('Microsoft YaHei UI', -9), anchor='w', tags='map-chrome')
        x += 57


def draw_waypoint_labels(canvas, viewport, points, labels):
    for label in labels:
        active, box = label['active'], label['rect']
        color = MAP_STYLE['navigation' if active else 'route']
        for index in label['indexes']:
            point = points[index-1]['position']
            x, y = viewport.screen(point[0], point[2])
            canvas.create_rectangle(x-2.5, y-2.5, x+2.5, y+2.5,
                                    fill=color, outline='#15271f', width=1, tags='saved-point')
        if box is None:
            continue
        x, y = label['anchor']
        bx, by = (box[0]+box[2])/2, (box[1]+box[3])/2
        canvas.create_line(x, y, bx, by, fill=color if active else '#5d7987', width=1,
                           tags='saved-point-leader')
        canvas.create_rectangle(*box, fill='#20453c' if active else '#182c32',
                                outline=color, width=1.5 if active else 1, tags='saved-point-badge')
        canvas.create_text(bx, by, text=label['caption'], fill='#eaf4ea',
                           font=('Consolas', -11, 'bold'), tags='saved-point-label')


def draw_target_indicator(canvas, viewport, indicator, number):
    x, y, dx, dy, distance = indicator
    canvas.create_oval(x-10, y-10, x+10, y+10, fill=MAP_STYLE['panel'],
                       outline=MAP_STYLE['navigation'], tags='target-indicator')
    canvas.create_line(x-dx*5, y-dy*5, x+dx*6, y+dy*6, fill=MAP_STYLE['navigation'],
                       width=2, arrow='last', arrowshape=(5, 6, 3), tags='target-indicator')
    text = canvas.create_text(x-dx*18, y-dy*18, text=f'目标 {number} · {distance:.0f} 单位',
                       anchor='e' if dx > .4 else 'w' if dx < -.4 else 's' if dy > 0 else 'n',
                       fill=MAP_STYLE['navigation'], font=('Microsoft YaHei UI', -10),
                       tags='target-indicator')
    bounds = canvas.bbox(text)
    if bounds:
        mx = max(6-bounds[0], min(0, viewport.width-6-bounds[2]))
        my = max(6-bounds[1], min(0, viewport.height-6-bounds[3]))
        canvas.move(text, mx, my)


def movement_direction(trail):
    """Last real movement in one continuous segment, never a camera-facing claim."""
    if len(trail) < 2:
        return None
    last = trail[-1]
    for index in range(len(trail)-2, -1, -1):
        previous = trail[index]
        if previous['segment'] != last['segment']:
            break
        dx, dz = last['x'] - previous['x'], previous['z'] - last['z']
        length = math.hypot(dx, dz)
        if length >= .05:
            return dx / length, dz / length
    return None


def draw_player(canvas, x, z, trail, paused=False, direction=None):
    color = '#8fa99a' if paused else MAP_STYLE['player']
    canvas.create_oval(x-6, z-6, x+6, z+6, fill='', outline='#718c6a' if paused else '#91ad73',
                       width=1, tags='player-ring')
    direction = direction if direction is not None else movement_direction(trail)
    if direction is not None:
        dx, dz = direction
        canvas.create_line(x+dx*4, z+dz*4, x+dx*14, z+dz*14,
                           fill=color, width=1.5, arrow='last', arrowshape=(4, 5, 2.5),
                           tags='player-heading')
    canvas.create_oval(x-2.5, z-2.5, x+2.5, z+2.5, fill=color, outline='', tags='player-position')


# Approximate color boundaries from the icon-reduced, registered map.
# Pixel origin (511, 459), scale 1.55. These polygons only tint the display.
_TERRAIN_TINTS = (
    ('upland', '#B6A184', ((228,100),(253,36),(451,38),(509,72),(581,66),
                          (621,100),(673,135),(696,205),(682,261),(613,233),
                          (542,224),(486,224),(412,200),(349,194),(302,231),(250,249),(239,218))),
    ('forest', '#96A779', ((706,228),(738,213),(782,224),(824,230),(845,257),
                          (863,296),(867,345),(879,400),(891,437),(868,466),
                          (860,518),(817,529),(768,508),(731,481),(724,423),
                          (721,363),(704,313),(696,266))),
    ('town', '#C7BDA4', ((398,382),(426,356),(496,354),(539,377),(577,394),
                        (602,429),(610,464),(595,498),(558,512),(507,504),
                        (472,516),(416,495),(389,454),(385,422))),
)


def draw_terrain_tints(cache, main_map):
    if not main_map:
        return
    left, right, low, high = cache.viewport.bounds()
    # Integer world cells pin grass marks to terrain across redraws.
    if cache.viewport.scale >= 1.5:
        for row in range(math.floor(low/12), math.ceil(high/12)):
            for column in range(math.floor(left/12), math.ceil(right/12)):
                x = column*12 + ((row*13 + column*7) % 9)
                z = row*12 + ((row*5 - column*11) % 9)
                key = ('grass-grain', column, row)
                cache.geometry.valid_keys.add(key)
                cache.polyline(key, (x,z,x+1.7,z+.5), -4.5,
                               [{'fill': '#9EAE81', 'width': 1, 'tags': 'map-texture'}])
    for name, fill, pixels in _TERRAIN_TINTS:
        key = ('terrain-tint', name)
        cache.geometry.valid_keys.add(key)
        coords = tuple(v for x, y in pixels for v in ((x-511)/1.55, (459-y)/1.55))
        cache.polygon(key, coords, -5, fill=fill, outline='', tags='terrain-tint')
        draw_map_texture(cache, key, coords, -4, '#AAA18B', 9, 2.5)


@lru_cache(maxsize=256)
def texture_strokes(coords, spacing, length):
    """Sparse world-aligned dashes clipped to a polygon."""
    points = tuple(zip(coords[::2], coords[1::2]))
    low, high = min(coords[1::2]), max(coords[1::2])
    spacing = max(spacing, (high-low)/10)
    strokes = []
    edges = tuple(zip(points, points[1:] + points[:1]))
    for row in range(math.ceil(low/spacing), math.ceil(high/spacing)):
        z = row*spacing
        crossings = sorted(a[0]+(b[0]-a[0])*(z-a[1])/(b[1]-a[1])
                           for a,b in edges if (a[1]>z) != (b[1]>z))
        for left,right in zip(crossings[::2], crossings[1::2]):
            pitch = max(length*4, (right-left)/5)
            for column in range(math.ceil(left/pitch), math.ceil(right/pitch)):
                x = column*pitch + (row % 3)*length/3
                if left+length*.3 < x < right-length*1.3:
                    strokes.append((x,z,x+length,z))
    stride = max(1, math.ceil(len(strokes)/10))
    return tuple(strokes[::stride])


def draw_map_texture(cache, key, coords, rank, color, spacing, length):
    strokes = texture_strokes(coords, spacing, length)
    # Dashes shorter than two screen pixels merge into the fill. Keep a few
    # spaced marks on large bodies, and use all marks at inspection scale.
    coarse = length * cache.viewport.scale < 2
    if coarse:
        if rank == -1.5 and len(strokes) < 7:
            return
        strokes = strokes[::4]
    cache.texture((*key, 'texture', coarse), strokes, rank, color)
