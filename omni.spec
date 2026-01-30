# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Collect all necessary files
datas = [
    ('assets/omni.png', 'assets'),
    ('src', 'src'),
    ('requirements.txt', '.'),
]

# Hidden imports to ensure they are packaged
hiddenimports = [
    'engineio.async_drivers.threading',
    'win32timezone',
    'pystray',
    'PIL',
    'BlurWindow',
    'keyboard',
    'flask',
    'requests',
    'sentence_transformers',
    'lancedb',
    'memvid_sdk',
    'llama_cpp'
]

# Add any dynamic imports here
tmp_ret = collect_all('sentence_transformers')
datas += tmp_ret[0]; hiddenimports += tmp_ret[1]

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Omni',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False, # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/omni.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Omni',
)
