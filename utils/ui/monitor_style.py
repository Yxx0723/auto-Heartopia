"""Shared palette, category labels and view defaults; no widget construction."""
from utils.ui.ui_tokens import COLORS as UI_COLORS

# 场景符号色只属于地图，UI 色全部来自 ui_tokens。深空底 + 霓虹描边。
COLORS = {**UI_COLORS,
          'shrub': '#0f3a34', 'shrub_outline': '#39ff9e',
          'blueberry': '#122c4d', 'blueberry_outline': '#4fc3ff',
          'raspberry': '#3d1030', 'raspberry_outline': '#ff5fa8'}

PLANT_STYLES = {'blueberry': (COLORS['blueberry'], COLORS['blueberry_outline']),
                'raspberry': (COLORS['raspberry'], COLORS['raspberry_outline']),
                'bush': (COLORS['shrub'], COLORS['shrub_outline']),
                'tree': ('#0f3b3a', '#5cffc8'),
                'shrub': ('#2b3326', '#c8f06a')}

FAUNA_STYLES = {'butterfly': ('#2a1650', '#c084ff'),
                'fish': ('#052e3d', '#3fe0ff'),
                'insect': ('#3a2f10', '#ffe066'),
                'bird': ('#0d3838', '#66f0e0'),
                'animal': ('#3d2a16', '#ffab5e')}

MINERAL_STYLES = {'rock': ('#24343d', '#9fc0cc'),
                  'ore': ('#33352a', '#ffd166'), 'fluorite': ('#06414a', '#5cfff2')}

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
                    'fluorite': ('无暇萤石', '#5cfff2'),
                    'roaming_oak': ('溜溜橡木', '#e8b574'),
                    'butterfly': ('蝴蝶', FAUNA_STYLES['butterfly'][1]),
                    'insect': ('其他昆虫', FAUNA_STYLES['insect'][1]),
                    'bird': ('鸟类', FAUNA_STYLES['bird'][1]),
                    'fish': ('鱼影', FAUNA_STYLES['fish'][1]),
                    'resource': ('其他资源点', '#c8dff0'),
                    'crop': ('作物', '#7dffb2'),
                    'animal': ('小动物', FAUNA_STYLES['animal'][1]),
                    'shrub': ('未确认果灌', PLANT_STYLES['shrub'][1]),
                    'other': ('其他物体', '#7f9fae')}
