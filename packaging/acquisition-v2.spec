# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


PROJECT_ROOT = Path(SPECPATH).parent
ENTRY_POINT = PROJECT_ROOT / "apps" / "acquisition_server" / "main.py"
SOURCE_ROOT = PROJECT_ROOT / "src"

datas = []
binaries = []
hiddenimports = []
for package in (
    "caproto",
    "cryptography",
    "dns",
    "h5py",
    "paramiko",
    "pymongo",
    "zmq",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(SOURCE_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "matplotlib", "pandas"],
    noarchive=False,
    optimize=0,
)
# The packaged default registry is the approved formal 47-device registry.
a.datas.append(("config/devices.json", str(PROJECT_ROOT / "config/devices.probe47.json"), "DATA"))
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="waveform-acquisition-v2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="waveform-acquisition-v2",
)
