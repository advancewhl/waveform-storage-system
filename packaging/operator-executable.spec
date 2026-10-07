# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
datas = []
binaries = []
hiddenimports = []
for package in (
    "PyQt5", "caproto", "cryptography", "h5py", "matplotlib",
    "numpy", "paramiko", "zmq",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(ROOT / "apps/operator_gui/main.py")],
    pathex=[str(ROOT / "src"), str(ROOT / "apps/operator_gui")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "IPython", "pytest", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="waveform-operator", console=False, upx=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas, name="waveform-operator", upx=False,
)
