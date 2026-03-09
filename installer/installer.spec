# installer.spec — PyInstaller spec for the Omni app (setup wizard + launcher).
# Bundles the PyQt6 wizard + entire Omni source tree as data resources.
# Output: dist/Omni.app  (drag to /Applications; first-run shows setup wizard)

import os
from pathlib import Path

# Resolve paths relative to this spec file
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
PROJECT_ROOT = os.path.dirname(SPEC_DIR)

# Collect Omni source files to embed as _omni_src data
datas = [
    # Core source tree
    (os.path.join(PROJECT_ROOT, "src"),          "_omni_src/src"),
    (os.path.join(PROJECT_ROOT, "assets"),       "_omni_src/assets"),
    (os.path.join(PROJECT_ROOT, "requirements.txt"), "_omni_src"),
    (os.path.join(PROJECT_ROOT, "run.sh"),        "_omni_src"),
    (os.path.join(PROJECT_ROOT, "run.py"),        "_omni_src"),
    (os.path.join(PROJECT_ROOT, "setup_index.sh"), "_omni_src"),
    # Wizard assets (fonts, logo bundled from assets/)
    (os.path.join(PROJECT_ROOT, "assets", "Manrope"),          "assets/Manrope"),
    (os.path.join(PROJECT_ROOT, "assets", "Instrument_Serif"), "assets/Instrument_Serif"),
    (os.path.join(PROJECT_ROOT, "assets", "omni.png"),         "assets"),
    # Plist templates
    (os.path.join(SPEC_DIR, "resources", "Info.plist"),        "resources"),
    (os.path.join(SPEC_DIR, "resources", "LaunchAgent.plist"), "resources"),
    (os.path.join(SPEC_DIR, "resources", "app_launcher.sh"),   "resources"),
]

a = Analysis(
    [os.path.join(SPEC_DIR, "main.py")],
    pathex=[SPEC_DIR, PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=["PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch", "torchvision", "torchaudio",
        "flask", "numpy", "scipy", "lancedb",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Omni",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=os.path.join(SPEC_DIR, "resources", "entitlements.plist"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Omni",
)

app = BUNDLE(
    coll,
    name="Omni.app",
    icon=os.path.join(PROJECT_ROOT, "assets", "omni.png"),
    bundle_identifier="com.omni.app",
    info_plist={
        "CFBundleName": "Omni",
        "CFBundleDisplayName": "Omni",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSPrincipalClass": "NSApplication",
        "NSAppleScriptEnabled": False,
    },
)
