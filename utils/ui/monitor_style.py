"""Shared palette, category labels and view defaults; no widget construction."""
from utils.ui.map_presentation import MAP_STYLE


COLORS = {'bg': '#101714', 'card': '#19221d', 'plot': MAP_STYLE['ground'],
          'line': '#2b382f', 'text': '#edf3e8', 'muted': '#9ba99c', 'green': '#cef18b',
          'red': '#f1a19a', 'blue': '#a8c8ee', 'amber': '#efc97b', 'button': '#202c24',
          'shrub': '#365d3e', 'shrub_outline': '#89c880',
          'blueberry': '#294a72', 'blueberry_outline': '#83b4ff',
          'raspberry': '#6a2944', 'raspberry_outline': '#ed91b3'}

PLANT_STYLES = {'blueberry': (COLORS['blueberry'], COLORS['blueberry_outline']),
                'raspberry': (COLORS['raspberry'], COLORS['raspberry_outline']),
                'bush': (COLORS['shrub'], COLORS['shrub_outline']),
                'tree': ('#326046', '#acce89'),
                'shrub': ('#3f4230', '#c3be83')}

FAUNA_STYLES = {'butterfly': ('#45345b', '#bd91f2'),
                'fish': ('#164b60', '#73dcf5'),
                'insect': ('#514528', '#e9c56b'),
                'bird': ('#26484e', '#6bc9d7'),
                'animal': ('#58422f', '#e8a775')}

MINERAL_STYLES = {'rock': ('#4d5b63', '#b0bec6'),
                  'ore': ('#4d5146', '#e2c17d'), 'fluorite': ('#176e77', '#80f5ed')}

MAP_PIXELS_PER_UNIT = 6.2208  # User-selected view: 3 * 1.2**4 pixels/unit, 10 units/grid.

SCENE_RANGE_OPTIONS = (80, 150, 200, 300, 400)

SCENE_RANKS = {**dict.fromkeys(PLANT_STYLES, 1), **dict.fromkeys(MINERAL_STYLES, 1),
               **dict.fromkeys(FAUNA_STYLES, 2), 'roaming_oak': 3}

SCENE_CATEGORIES = {'blueberry': ('蓝莓', COLORS['blueberry_outline']),
                    'raspberry': ('树莓', COLORS['raspberry_outline']),
                    'bush': ('普通灌木', COLORS['shrub_outline']),
                    'tree': ('树木', PLANT_STYLES['tree'][1]),
                    'rock': ('岩石', MINERAL_STYLES['rock'][1]),
                    'ore': ('矿石', MINERAL_STYLES['ore'][1]),
                    'fluorite': ('无暇萤石', '#80f5ed'),
                    'roaming_oak': ('溜溜橡木', '#e8b574'),
                    'butterfly': ('蝴蝶', FAUNA_STYLES['butterfly'][1]),
                    'insect': ('其他昆虫', FAUNA_STYLES['insect'][1]),
                    'bird': ('鸟类', FAUNA_STYLES['bird'][1]),
                    'fish': ('鱼影', FAUNA_STYLES['fish'][1]),
                    'resource': ('其他资源点', '#dfd19c'),
                    'crop': ('作物', '#a8e07c'),
                    'animal': ('小动物', FAUNA_STYLES['animal'][1]),
                    'shrub': ('未确认果灌', PLANT_STYLES['shrub'][1]),
                    'other': ('其他物体', '#91a69a')}
