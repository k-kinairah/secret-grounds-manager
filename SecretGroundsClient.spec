# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('client')
hiddenimports += collect_submodules('server')

python_root = Path(sys.base_prefix)


def collect_tree(source, target):
    source = Path(source)
    if not source.is_dir():
        raise FileNotFoundError(f"Required Tcl/Tk source folder is missing: {source}")
    return [
        (str(path), str(Path(target) / path.relative_to(source).parent))
        for path in source.rglob("*")
        if path.is_file()
    ]


datas = [
    ('VERSION', '.'),
    ('client\\config.example.json', '.'),
]
datas += collect_tree(python_root / 'tcl' / 'tcl8.6', '_tcl_data')
datas += collect_tree(python_root / 'tcl' / 'tk8.6', '_tk_data')
datas += collect_tree(python_root / 'Lib' / 'tkinter', 'tkinter')

binaries = []
for dll_name in ('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll'):
    dll_path = python_root / 'DLLs' / dll_name
    if not dll_path.is_file():
        raise FileNotFoundError(f"Required Tcl/Tk binary is missing: {dll_path}")
    binaries.append((str(dll_path), '.'))


a = Analysis(
    ['scripts\\pyinstaller_client_entry.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='SecretGroundsClient',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SecretGroundsClient',
)
