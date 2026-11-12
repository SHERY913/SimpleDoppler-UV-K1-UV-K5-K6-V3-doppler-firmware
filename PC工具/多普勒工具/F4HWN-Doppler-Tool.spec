# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['../../PC工具/多普勒工具/doppler_tool.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/13766/Documents/deepseek-harness/default-workspace/多普勒/PC工具/多普勒工具/sat_pics', 'sat_pics'),
           ('C:/Users/13766/Documents/deepseek-harness/default-workspace/多普勒/PC工具/多普勒工具/app.ico', '.'),
           ('C:/Users/13766/Documents/deepseek-harness/default-workspace/多普勒/PC工具/多普勒工具/ico', 'ico'),
           ('C:/Users/13766/Documents/deepseek-harness/default-workspace/多普勒/PC工具/多普勒工具/lang_en.json', '.')],
    hiddenimports=['sgp4.api', 'serial', 'serial.tools.list_ports'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='F4HWN-Doppler-Tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='C:/Users/13766/Documents/deepseek-harness/default-workspace/多普勒/PC工具/多普勒工具/app.ico',
)
