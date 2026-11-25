# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import subprocess

root = Path(SPECPATH).resolve().parent
build_id = Path(workpath) / 'heartopia-build.txt'
try:
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
except (OSError, subprocess.CalledProcessError):
    revision = 'unknown'
build_id.write_text(revision, encoding='utf-8')
icon = root / 'packaging/assets/monitor.ico'
a = Analysis([str(root / 'tools/coordinate_monitor.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'version.txt'), '.'), (str(build_id), '.'),
                         (str(root / 'webui/coordinate-monitor/profile.json'), 'monitor'),
                         (str(root / 'webui/coordinate-monitor/road-mask.png'), 'monitor'),
                         (str(root / 'webui/coordinate-monitor/road-mask-calibration.json'), 'monitor'),
                         (str(root / 'data/cooking/cooking_catalog.json'), 'data/cooking'),
                         (str(root / 'data/routes/蓝莓.json'), 'data/routes'),
                         (str(icon), 'monitor'),
                         (str(root/'webui/coordinate-monitor/cooking-coffee.json'), 'monitor'),
                         (str(root/'webui/coordinate-monitor/repair_box_menu.png'), 'monitor'),
                         (str(root/'webui/coordinate-monitor/repair_menu_marker.png'), 'monitor'),
                         (str(root/'webui/coordinate-monitor/axe_pick_icon.png'), 'monitor'),
                         (str(root/'webui/coordinate-monitor/tool_tablet_marker.png'), 'monitor'),
                         *[(str(root/'webui/coordinate-monitor'/name), 'monitor') for name in (
                             'food_star.png', 'food_search_header.png', 'food_search_results.png', 'food_search_end.png', 'food_bag_marker.png', 'food_bag_close.png', 'food_eat_button.png', 'food_category.png', 'fruit_apple.png',
                             'fruit_blueberry.png', 'fruit_raspberry.png', 'fruit_orange.png')]], hiddenimports=[], hookspath=[],
    hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='HeartopiaMonitor',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True, upx_exclude=[],
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
    uac_admin=True, icon=[str(icon)])
