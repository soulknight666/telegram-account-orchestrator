# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
ICON = ROOT / "build" / "branding" / "tao.ico"

a = Analysis(
    [str(ROOT / "packaging" / "launcher_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "tam" / "web"), "tam/web"),
        (str(ROOT / "tam" / "assets"), "tam/assets"),
        (str(ROOT / ".env.example"), "."),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "NOTICE.GAFBot"), "."),
    ],
    hiddenimports=collect_submodules("tam"),
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "setuptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TAO-Launcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ICON),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TAO-Windows-x64-Portable",
)
