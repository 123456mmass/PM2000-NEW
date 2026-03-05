# -*- mode: python ; coding: utf-8 -*-
import site
import os

# Find pycloudflared in any site-packages (venv or system)
pycloudflared_path = None
for sp in site.getsitepackages():
    candidate = os.path.join(sp, 'pycloudflared')
    if os.path.isdir(candidate):
        pycloudflared_path = candidate
        break

if pycloudflared_path is None:
    raise SystemExit("ERROR: pycloudflared not found in any site-packages. Run: python -m pip install pycloudflared")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('.env', '.'),
        (pycloudflared_path, 'pycloudflared'),
    ],
    hiddenimports=['pycloudflared'],
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
    a.binaries,
    a.datas,
    [],
    name='backend-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
