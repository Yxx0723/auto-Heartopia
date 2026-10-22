# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).resolve().parent
icon = root / 'packaging/assets/monitor.ico'
a = Analysis([str(root / 'tools/coordinate_monitor.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'resources/monitor/profile.json'), 'monitor'),
                         (str(icon), 'monitor'),
                         (str(root/'resources/monitor/repair_box_menu.png'), 'monitor'),
                         (str(root/'resources/monitor/repair_menu_marker.png'), 'monitor'),
                         (str(root/'resources/monitor/axe_pick_icon.png'), 'monitor'),
                         (str(root/'resources/monitor/tool_tablet_marker.png'), 'monitor'),
                         *[(str(root/'resources/monitor'/name), 'monitor') for name in (
                             'food_search_header.png', 'food_search_results.png', 'food_search_end.png', 'food_bag_marker.png', 'food_bag_close.png', 'food_eat_button.png', 'food_star.png', 'food_category.png', 'fruit_apple.png',
                             'fruit_blueberry.png', 'fruit_raspberry.png', 'fruit_orange.png')]], hiddenimports=[], hookspath=[],
    hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='HeartopiaMonitor',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True, upx_exclude=[],
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
    uac_admin=True, icon=[str(icon)])
