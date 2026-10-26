"""Lightweight cartographic overlays in native Tk, with no game-side effects."""
import math


MAP_STYLE = {'ground': '#17251f', 'grid': '#25392e', 'grid_major': '#31473a',
             'edge': '#354b3e', 'panel': '#132019', 'muted': '#8aab96',
             'water': '#1e5b91', 'shore': '#58a8d6', 'shore_outer': '#244c68',
             'collision': '#4b6050', 'road': '#8a8163', 'route': '#a1c8ee',
             'route_line': '#728fa6',
             'navigation': '#74e3d1', 'trail': '#689b7c', 'player': '#d0ef9b',
             'fence': '#e6b96e', 'handrail': '#91cbdc'}


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
