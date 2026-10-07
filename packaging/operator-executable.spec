# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
datas = []
binaries = []
hiddenimports = []
for package in (
    "PyQt5", "caproto", "cryptography", "h5py", "matplotlib",
    "numpy", "pandas", "scipy", "paramiko", "zmq",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
datas += [(str(ROOT / "src/waveform_storage/analysis/pv_engine/algorithm-presets.json"),
           "waveform_storage/analysis/pv_engine")]

a = Analysis(
    [str(ROOT / "apps/operator_gui/main.py")],
    pathex=[str(ROOT / "src"), str(ROOT / "apps/operator_gui")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "IPython", "pytest"],
    noarchive=False,
)
# A Conda pyexpat extension must use its matching Expat ABI, not an older
# system copy discovered through another bundled extension.
environment_expat = Path(sys.prefix) / "lib/libexpat.so.1"
if environment_expat.is_file():
    a.binaries = [entry for entry in a.binaries if entry[0] != "libexpat.so.1"]
    a.binaries.append(("libexpat.so.1", str(environment_expat.resolve()), "BINARY"))
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="waveform-operator", console=False, upx=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas, name="waveform-operator", upx=False,
)
