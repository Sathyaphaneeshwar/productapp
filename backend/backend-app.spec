# -*- mode: python ; coding: utf-8 -*-
import os

# Use os.path.join for cross-platform compatibility
BASE_DIR = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        (os.path.join('..', 'database'), 'database'),
        (os.path.join('..', 'data'), 'data'),
    ],
    hiddenimports=[
        'xhtml2pdf',
        'xhtml2pdf.files',
        'xhtml2pdf.w3c',
        'reportlab',
        'html5lib',
        'pypdf',
        'pdfplumber',
        'PIL',
        'flask_cors',
    ],
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
    name='backend-app',
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
    name='backend-app',
)
