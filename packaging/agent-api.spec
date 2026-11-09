# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


PROJECT_ROOT = Path(SPECPATH).parent
ENTRY_POINT = PROJECT_ROOT / "tools" / "run_waveform_agent_api.py"
SOURCE_ROOT = PROJECT_ROOT / "src"
APPS_ROOT = PROJECT_ROOT / "apps" / "operator_gui"

datas = []
binaries = []
hiddenimports = [
    "candidate_backend",
    "waveform_storage.analysis.loaders",
    "waveform_storage.analysis.numerics",
]
for package in (
    "caproto",
    "cryptography",
    "dns",
    "fastapi",
    "h5py",
    "numpy",
    "pandas",
    "paramiko",
    "pydantic",
    "pymongo",
    "scipy",
    "starlette",
    "uvicorn",
    "zmq",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# The analysis engine resolves these files relative to its package directory.
for path in (SOURCE_ROOT / "waveform_storage").rglob("*.json"):
    datas.append((str(path), str(path.parent.relative_to(SOURCE_ROOT))))
datas.append(
    (
        str(SOURCE_ROOT / "waveform_storage" / "processing" / "alignment.py"),
        "waveform_storage/processing",
    )
)

a = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(SOURCE_ROOT), str(APPS_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "matplotlib"],
    noarchive=False,
    optimize=0,
)
a.datas.append(
    (
        "config/devices.probe47.json",
        str(PROJECT_ROOT / "config" / "devices.probe47.json"),
        "DATA",
    )
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="waveform-agent-api",
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
    name="waveform-agent-api",
)
