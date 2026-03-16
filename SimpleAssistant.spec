# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for SimpleAssistant (简单助手).
Build command:  pyinstaller SimpleAssistant.spec
Output:         dist\SimpleAssistant\SimpleAssistant.exe  (no console window)
"""

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # PyQt5
        'PyQt5',
        'PyQt5.QtWidgets',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.sip',
        # Audio
        'sounddevice',
        'soundfile',
        'soundfile._soundfile',
        'numpy',
        'numpy.core._multiarray_umath',
        # Input / clipboard
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',
        'pynput.mouse',
        'pynput.mouse._win32',
        'pyperclip',
        'pyperclip.handlers',
        # Network / AI
        'openai',
        'anthropic',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        # Standard library extras
        'sqlite3',
        'json',
        'threading',
        'ctypes',
        'ctypes.wintypes',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'PIL',
        'cv2',
        'test',
        'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SimpleAssistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can break some DLLs; keep off for safety
    console=False,      # ← 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # 可换成 'icon.ico'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SimpleAssistant',
)
