# -*- mode: python ; coding: utf-8 -*-
import os

# Use os.path.join for cross-platform compatibility
# SPEC is the path to this spec file
BASE_DIR = os.path.dirname(os.path.abspath(SPEC))
PROJECT_ROOT = os.path.dirname(BASE_DIR)  # Parent of backend folder

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        (os.path.join(PROJECT_ROOT, 'database'), 'database'),
        (os.path.join(PROJECT_ROOT, 'data'), 'data'),
    ],
    hiddenimports=[
        # Flask & Web
        'flask',
        'flask_cors',
        
        # xhtml2pdf and dependencies
        'xhtml2pdf',
        'xhtml2pdf.files',
        'xhtml2pdf.w3c',
        'xhtml2pdf.context',
        'xhtml2pdf.parser',
        'xhtml2pdf.tables',
        'xhtml2pdf.tags',
        'xhtml2pdf.document',
        'xhtml2pdf.default',
        
        # reportlab - comprehensive
        'reportlab',
        'reportlab.graphics',
        'reportlab.graphics.barcode',
        'reportlab.graphics.barcode.code128',
        'reportlab.graphics.barcode.code39',
        'reportlab.graphics.barcode.code93',
        'reportlab.graphics.barcode.common',
        'reportlab.graphics.barcode.eanbc',
        'reportlab.graphics.barcode.ecc200datamatrix',
        'reportlab.graphics.barcode.fourstate',
        'reportlab.graphics.barcode.lto',
        'reportlab.graphics.barcode.qr',
        'reportlab.graphics.barcode.qrencoder',
        'reportlab.graphics.barcode.usps',
        'reportlab.graphics.barcode.usps4s',
        'reportlab.graphics.barcode.widgets',
        'reportlab.lib',
        'reportlab.lib.colors',
        'reportlab.lib.pagesizes',
        'reportlab.lib.units',
        'reportlab.pdfgen',
        'reportlab.platypus',
        
        # PDF processing
        'html5lib',
        'pypdf',
        'pdfplumber',
        'PIL',
        'PIL.Image',
        
        # Markdown
        'markdown',
        'markdown.extensions',
        'markdown.extensions.tables',
        'markdown.extensions.fenced_code',
        
        # LLM providers
        'anthropic',
        'openai',
        'google.generativeai',
        'google.ai.generativelanguage',
        
        # Tokenization
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
        
        # Web scraping & networking
        'requests',
        'bs4',
        'beautifulsoup4',
        
        # Cryptography
        'cryptography',
        'cryptography.fernet',
        
        # Email
        'email',
        'email.mime',
        'email.mime.text',
        'email.mime.multipart',
        'smtplib',
        
        # Standard library that might be missed
        'sqlite3',
        'json',
        'threading',
        'io',
        'html',
        're',
        'random',
        'time',
        'sys',
        'os',
        'datetime',
        'dataclasses',
        'abc',
        'typing',
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
