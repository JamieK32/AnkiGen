# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path.cwd()
icon_path = project_root / "images" / "app_icon.ico"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("README.md", "."),
        ("LICENSE", "."),
        (".env.example", "."),
        ("images/app_icon.png", "images"),
        ("images/app_icon.ico", "images"),
    ],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name="AnkiGen",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AnkiGen",
)
