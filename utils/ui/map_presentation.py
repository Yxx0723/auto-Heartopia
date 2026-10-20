"""Lightweight cartographic overlays in native Tk, with no game-side effects."""
import math


MAP_STYLE = {'ground': '#17251f', 'grid': '#25392e', 'grid_major': '#31473a',
             'edge': '#354b3e', 'panel': '#132019', 'muted': '#8aab96',
             'water': '#183e4b', 'shore': '#47929a', 'shore_outer': '#254f52',
             'collision': '#666e58', 'road': '#8a8163', 'route': '#a1c8ee',
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


def draw_chrome(canvas, viewport, step, route_view=False, reference_roads=True, heading_known=False):
    w, h = viewport.width, viewport.height
    panel, edge, muted = MAP_STYLE['panel'], MAP_STYLE['edge'], MAP_STYLE['muted']
    canvas.create_rectangle(12, 12, 170, 51, fill=panel, outline=edge, tags='map-chrome')
    canvas.create_text(24, 25, text='路线全览' if route_view else '附近地图',
                       fill='#d7e5cd', font=('Microsoft YaHei UI', -12, 'bold'),
                       anchor='w', tags='map-chrome')
    canvas.create_text(24, 41, text='角色跟随 · 箭头为行进方向' if heading_known else '走动后显示方向箭头',
                       fill=muted, font=('Microsoft YaHei UI', -9), anchor='w', tags='map-chrome')
    # These are world axes, not an assumed geographic north or camera heading.
    x, z = w - 37, 37
    canvas.create_oval(x-22, z-22, x+22, z+22, fill=panel, outline=edge, tags='map-chrome')
    canvas.create_line(x, z+10, x, z-12, fill='#b8d0b3', width=2, arrow='last',
                       arrowshape=(5, 6, 3), tags='map-chrome')
    canvas.create_line(x-10, z, x+12, z, fill='#759780', arrow='last',
                       arrowshape=(4, 5, 2), tags='map-chrome')
    canvas.create_text(x, z-28, text='+Z', fill=muted, font=('Consolas', -9), tags='map-chrome')
    canvas.create_text(x+26, z, text='+X', fill=muted, font=('Consolas', -9), tags='map-chrome')
    # A true scale bar derived from world-to-screen projection.
    pixels = step * viewport.scale
    x, z = 19, h - 19
    canvas.create_rectangle(12, h-35, max(128, pixels+82), h-8,
                            fill=panel, outline='', tags='map-chrome')
    canvas.create_line(x, z, x+pixels, z, fill='#cbdabf', width=3, tags='map-scale')
    for end in (x, x+pixels):
        canvas.create_line(end, z-4, end, z+3, fill='#cbdabf', width=1, tags='map-scale')
    canvas.create_text(x+pixels+8, z, text=f'{step:g} 单位', fill=muted,
                       font=('Microsoft YaHei UI', -10), anchor='w', tags='map-scale')
    if w >= 570:
        labels = (('角色', MAP_STYLE['player']), ('点位', MAP_STYLE['route']),
                  ('寻路', MAP_STYLE['navigation']), ('水域', MAP_STYLE['shore']),
                  ('道路参考', MAP_STYLE['road']))
        x = w - 318
        canvas.create_rectangle(x-9, h-35, w-12, h-8, fill=panel, outline='', tags='map-chrome')
        for label, color in labels:
            canvas.create_line(x, z, x+8, z, fill=color, width=3, tags='map-chrome')
            canvas.create_text(x+13, z, text=label, fill=muted,
                               font=('Microsoft YaHei UI', -9), anchor='w', tags='map-chrome')
            x += 57


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
