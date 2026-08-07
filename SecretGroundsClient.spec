# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, get_package_paths

hiddenimports = []
hiddenimports += collect_submodules('client')
hiddenimports += collect_submodules('server')
hiddenimports += collect_submodules('webview')
hiddenimports += collect_submodules('webview.platforms')
hiddenimports += collect_submodules('pythonnet')
hiddenimports += collect_submodules('clr_loader')

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
    ('client\\assets\\secret-grounds-logo.png', 'assets'),
]
datas += collect_tree('client\\web_ui', 'client\\web_ui')
datas += collect_tree(python_root / 'tcl' / 'tcl8.6', '_tcl_data')
datas += collect_tree(python_root / 'tcl' / 'tk8.6', '_tk_data')
datas += collect_tree(python_root / 'Lib' / 'tkinter', 'tkinter')
_, webview_package_dir = get_package_paths('webview')
webview_lib_dir = Path(webview_package_dir) / 'lib'
webview_edgechromium_files = [
    Path('Microsoft.Web.WebView2.Core.dll'),
    Path('Microsoft.Web.WebView2.WinForms.dll'),
    Path('WebBrowserInterop.x64.dll'),
    Path('WebBrowserInterop.x86.dll'),
    Path('runtimes') / 'win-arm64' / 'native' / 'WebView2Loader.dll',
    Path('runtimes') / 'win-x64' / 'native' / 'WebView2Loader.dll',
    Path('runtimes') / 'win-x86' / 'native' / 'WebView2Loader.dll',
]
for webview_file in webview_edgechromium_files:
    datas.append((str(webview_lib_dir / webview_file), str(Path('webview') / 'lib' / webview_file.parent)))
datas += collect_data_files('webview', subdir='js')

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
a.datas = [
    entry
    for entry in a.datas
    if not str(entry[0]).replace('\\', '/').endswith('webview/lib/pywebview-android.jar')
]
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
    # UPX-packed unsigned PyInstaller executables are one of the most common
    # Windows Defender / AV false-positive triggers (packed entropy reads the
    # same to ML heuristics whether the packer is hiding malware or just
    # shrinking a legitimate app). UPX buys a smaller file at the cost of a
    # much higher false-positive rate, and this codebase has no code-signing
    # certificate to offset that risk yet -- keep it off.
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='client\\version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SecretGroundsClient',
)
