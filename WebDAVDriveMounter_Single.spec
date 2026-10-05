# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['d:/G1/main.py'],
    pathex=[],
    binaries=[],
    datas=[('d:/G1/app_icon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['unittest', 'test', 'pydoc', 'sqlite3', 'xmlrpc', 'distutils', 'setuptools', 'pip', 'tkinter.test', 'lib2to3', 'asyncio', 'curses', 'email.test', 'pydoc_data', 'PIL._avif', 'PIL._imagingcms', 'PIL._imagingft', 'PIL._webp', 'PIL.ImageQt', 'PIL.ImageTk', 'PIL.PdfParser', 'wsgidav', 'cheroot', 'cryptography', 'bcrypt', 'jinja2', 'pyftpdlib'],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('O', None, 'OPTION'), ('O', None, 'OPTION')],
    name='WebDAVDriveMounter_Single',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['d:/G1/app_icon.ico'],
)
