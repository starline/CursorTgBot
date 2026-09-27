# -*- mode: python ; coding: utf-8 -*-
"""One folder app for Windows, Linux, and macOS. Build with packaging/*/build.*"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve().parent
icon = root / "packaging" / "windows" / "icon.ico"

datas: list = []
binaries: list = []
hiddenimports = [
    "bot",
    "bot.__main__",
    "bot.agent_runner",
    "bot.handlers",
    "bot.config",
    "bot.store",
    "bot.prompt",
    "bot.backlog_view",
    "desktop",
    "desktop.__main__",
    "desktop.app",
    "desktop.supervisor",
    "desktop.platform",
    "desktop.paths",
    "desktop.prefs",
    "desktop.settings",
    "desktop.tray",
    "desktop.instance",
    "desktop.logtail",
]

example = root / ".env.example"
if example.exists():
    datas.append((str(example), "."))

for package in ("aiogram", "cursor_sdk", "dotenv", "pystray", "PIL"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(root / "launch_desktop.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CursorTgBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(icon) if icon.exists() and sys.platform == "win32" else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CursorTgBot",
)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="CursorTgBot.app",
        bundle_identifier="com.cursortgbot.app",
    )
