# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller specification for Tiki Karaoke (Commercial Windows Edition)
Bundles FastAPI, Uvicorn, SQLite, Static UI Assets, HTML Templates,
and packaged 64-bit binaries (ffmpeg, ffprobe, cloudflared, bore).
"""
import os
import sys
from pathlib import Path

spec_dir = Path(SPECPATH).resolve()
# Check sibling repo first, then parent.parent
if (spec_dir.parent / "tiki-karaoke").exists():
    project_root = (spec_dir.parent / "tiki-karaoke").resolve()
else:
    project_root = spec_dir.parent.parent.resolve()

# Include static and template trees
datas = [
    (str(project_root / "static"), "static"),
    (str(project_root / "templates"), "templates"),
    (str(spec_dir / "tiki-icon.ico"), "."),
]

# Include bundled Windows binaries if staged
bin_dir = spec_dir / "bin"
if not bin_dir.exists():
    bin_dir = project_root / "bin"

if bin_dir.exists():
    for bin_file in bin_dir.glob("*.exe"):
        datas.append((str(bin_file), "bin"))

# Ensure tiki-mic-router.exe is bundled from mic_router/ if not already in bin/
mic_router_exe = spec_dir / "mic_router" / "tiki-mic-router.exe"
if mic_router_exe.exists() and not (bin_dir / "tiki-mic-router.exe").exists():
    datas.append((str(mic_router_exe), "bin"))

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# Hidden imports needed for Uvicorn dynamic protocol/loop loaders, yt-dlp, and system tray
hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "engineio.async_drivers.asgi",
    "qrcode",
    "qrcode.image.pil",
    "jinja2",
    "pydantic",
    "pydantic_core",
    "starlette",
    "fastapi",
    "sqlite3",
    "ctypes",
    "ctypes.wintypes",
    "wave",
    "winsound",
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "PIL.IcoImagePlugin",
    "PIL.PngImagePlugin",
    "tray",
    "audio_router",
    "network_helper",
    "party_packs",
    "control_hub",
    "tkinter",
    "tkinter.messagebox",
    "PIL.ImageTk",
    "importer",
    "importer.main",
    "importer.ui.app_window",
    "importer.ui.cdg_ripper_window",
    "importer.ui.compuhost_window",
    "importer.tiki_importer.engine",
    "importer.tiki_importer.config",
    "importer.tiki_importer.compuhost",
    "importer.tiki_importer.tps_reader",
    "importer.tiki_importer.cdg_ripper",
    "importer.tiki_importer.cleaner",
    "importer.tiki_importer.db_adapter",
    "importer.tiki_importer.enricher",
]

hiddenimports += collect_submodules("importer")

# Explicitly collect all yt_dlp submodules (including youtube extractors) and data files
hiddenimports += collect_submodules("yt_dlp")
datas += collect_data_files("yt_dlp")

block_cipher = None

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pytest"],
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
    name="TikiKaraoke",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Native windowed desktop application (Control Hub & System Tray)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(spec_dir / "tiki-icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TikiKaraoke",
)
