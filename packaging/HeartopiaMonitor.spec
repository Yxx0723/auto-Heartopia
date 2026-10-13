# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).resolve().parent
icon = root / 'packaging/assets/monitor.ico'
a = Analysis([str(root / 'tools/coordinate_monitor.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'webui/coordinate-monitor/profile.json'), 'monitor'),
                         (str(icon), 'monitor')], hiddenimports=[], hookspath=[],
    hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='HeartopiaMonitor',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True, upx_exclude=[],
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
    uac_admin=True, icon=[str(icon)])
