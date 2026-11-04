"""
routes/launcher.py - Desktop in-process UI and application launchers.

Provides endpoints to trigger or launch Tiki Karaoke desktop companion apps:
- Smart Media Importer
- CD+G Optical Disc Ripper
- Tiki Rehearsal Lounge
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

from config import get_db_path, get_tracks_dir
from services.auth import is_host_request, is_local_request
from services.lifecycle import (
    get_launch_cdg_ripper_trigger,
    get_launch_importer_trigger,
)

router = APIRouter(tags=["launcher"])


def _check_reg_key_for_exe(key, winreg_mod, exe_name: str) -> Optional[Path]:
    """Helper to check InstallLocation and UninstallString values inside a registry key."""
    try:
        inst_dir, _ = winreg_mod.QueryValueEx(key, "InstallLocation")
        if inst_dir:
            p = Path(str(inst_dir).strip(' "')) / exe_name
            if p.is_file():
                return p
    except OSError:
        pass

    for unins_val in ["QuietUninstallString", "UninstallString"]:
        try:
            unins, _ = winreg_mod.QueryValueEx(key, unins_val)
            if unins:
                clean_unins = str(unins).strip(' "')
                p = Path(clean_unins).parent / exe_name
                if p.is_file():
                    return p
        except OSError:
            pass

    return None


def _find_windows_registry_app(app_id: str, exe_name: str, display_name: str = "") -> Optional[Path]:
    """Queries Windows Registry uninstall and App Paths keys for an installed application."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    hives = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
    subpaths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]

    for hive in hives:
        for subpath in subpaths:
            # 1. Direct AppId key check (Inno Setup creates {GUID}_is1)
            try:
                with winreg.OpenKey(hive, f"{subpath}\\{app_id}") as key:
                    p = _check_reg_key_for_exe(key, winreg, exe_name)
                    if p:
                        return p
            except OSError:
                pass

            # 2. Enumerate uninstall keys for matching DisplayName
            if display_name:
                try:
                    with winreg.OpenKey(hive, subpath) as parent_key:
                        num_subkeys = winreg.QueryInfoKey(parent_key)[0]
                        for idx in range(num_subkeys):
                            try:
                                subkey_name = winreg.EnumKey(parent_key, idx)
                                with winreg.OpenKey(parent_key, subkey_name) as child_key:
                                    try:
                                        d_name, _ = winreg.QueryValueEx(child_key, "DisplayName")
                                        if d_name and display_name.lower() in str(d_name).lower():
                                            p = _check_reg_key_for_exe(child_key, winreg, exe_name)
                                            if p:
                                                return p
                                    except OSError:
                                        pass
                            except OSError:
                                continue
                except OSError:
                    pass

    # 3. App Paths check
    for hive in hives:
        for app_path_sub in [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
            rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
        ]:
            try:
                with winreg.OpenKey(hive, app_path_sub) as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    if val:
                        p = Path(str(val).strip(' "'))
                        if p.is_file():
                            return p
            except OSError:
                continue

    return None


def find_importer_executable() -> Optional[Tuple[List[str], Optional[Path]]]:
    """
    Locates the Tiki Karaoke Smart Importer executable or script.
    Returns (cmd_args, working_dir) or None.
    """
    current_dir = Path(__file__).resolve().parent.parent

    # 1. Bundled in same directory or bin (Windows .exe or Linux binary)
    if sys.platform == "win32":
        candidates = [
            current_dir / "TikiKaraokeImporter.exe",
            current_dir / "bin" / "TikiKaraokeImporter.exe",
            current_dir.parent / "TikiKaraokeImporter.exe",
            current_dir.parent / "bin" / "TikiKaraokeImporter.exe",
        ]
        if getattr(sys, "frozen", False):
            app_root = Path(sys.executable).resolve().parent
            candidates.extend([
                app_root / "TikiKaraokeImporter.exe",
                app_root / "bin" / "TikiKaraokeImporter.exe",
                app_root.parent / "Tiki Karaoke Importer" / "TikiKaraokeImporter.exe",
                app_root.parent / "TikiKaraokeImporter" / "TikiKaraokeImporter.exe",
            ])
        for c in candidates:
            if c.is_file():
                return ([str(c)], c.parent)

        # Windows Registry & standard Program Files checks
        reg_importer = _find_windows_registry_app("{8F15A89B-4D4B-4811-A5C7-D57B06AE11E1}_is1", "TikiKaraokeImporter.exe", "Tiki Karaoke")
        if reg_importer and reg_importer.is_file():
            return ([str(reg_importer)], reg_importer.parent)

        which_imp = shutil.which("TikiKaraokeImporter.exe") or shutil.which("TikiKaraokeImporter")
        if which_imp and Path(which_imp).is_file():
            p = Path(which_imp)
            return ([str(p)], p.parent)
    else:
        candidates = [
            current_dir / "TikiKaraokeImporter",
            current_dir.parent / "TikiKaraokeImporter",
            current_dir / "bin" / "TikiKaraokeImporter",
            current_dir.parent / "bin" / "TikiKaraokeImporter",
            Path("/usr/bin/TikiKaraokeImporter"),
        ]
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                return ([str(c)], c.parent)
        for c in sorted(current_dir.glob("TikiKaraokeImporter*.AppImage"), key=lambda p: p.stat().st_mtime, reverse=True):
            if c.is_file() and os.access(c, os.X_OK):
                return ([str(c)], c.parent)

    # 2. If running inside a standalone bundle (PyInstaller / AppImage), self-invoke with --importer
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        if exe_path.is_file():
            return ([str(exe_path), "--importer"], exe_path.parent)

    # 3. Check internal merged module in development mode: current_dir / "importer" / "main.py"
    internal_importer = current_dir / "importer"
    if (internal_importer / "main.py").is_file():
        return ([sys.executable, str(internal_importer / "main.py")], internal_importer)
    if (current_dir.parent / "importer" / "main.py").is_file():
        return ([sys.executable, str(current_dir.parent / "importer" / "main.py")], current_dir.parent / "importer")

    # 4. Check sibling repository: ../tiki-karaoke-importer
    importer_repo = current_dir.parent / "tiki-karaoke-importer"
    if importer_repo.is_dir():
        if sys.platform == "win32":
            win_exe = importer_repo / "dist" / "windows" / "TikiKaraokeImporter.exe"
            if win_exe.is_file():
                return ([str(win_exe)], win_exe.parent)
        else:
            for appimage in sorted((importer_repo / "dist").glob("TikiKaraokeImporter*.AppImage"), key=lambda p: p.stat().st_mtime, reverse=True):
                if appimage.is_file() and os.access(appimage, os.X_OK):
                    return ([str(appimage)], appimage.parent)

        main_py = importer_repo / "main.py"
        if main_py.is_file():
            return ([sys.executable, str(main_py)], importer_repo)

    return None


def find_rehearsal_lounge_executable() -> Optional[Tuple[List[str], Optional[Path]]]:
    """
    Locates the Tiki Rehearsal Lounge executable, AppImage, or development script.
    Checks Windows Registry, default Program Files installation directories, PATH,
    portable sibling folders, and dev worktrees.
    Returns (cmd_args, working_dir) or None.
    """
    current_dir = Path(__file__).resolve().parent.parent

    # 1. Windows platform detection
    if sys.platform == "win32":
        # A. Windows Registry check (Inno Setup AppId: {A4B7D6E2-81C5-4A39-9A0B-48F06E662F81})
        reg_exe = _find_windows_registry_app(
            "{A4B7D6E2-81C5-4A39-9A0B-48F06E662F81}_is1",
            "TikiRehearsalLounge.exe",
            "Tiki Rehearsal Lounge"
        )
        if reg_exe and reg_exe.is_file():
            return ([str(reg_exe)], reg_exe.parent)

        # B. System PATH check
        for bin_name in ["TikiRehearsalLounge.exe", "TikiRehearsalLounge"]:
            which_path = shutil.which(bin_name)
            if which_path and Path(which_path).is_file():
                p = Path(which_path)
                return ([str(p)], p.parent)

        # C. Common installation locations (Inno Setup default: {autopf}\Tiki Rehearsal Lounge)
        prog_roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramW6432"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
            r"D:\Program Files",
        ]
        local_app = os.environ.get("LOCALAPPDATA")
        app_data = os.environ.get("APPDATA")
        user_home = Path.home()

        search_dirs: List[Path] = []
        for root in prog_roots:
            if root:
                search_dirs.append(Path(root) / "Tiki Rehearsal Lounge")
                search_dirs.append(Path(root) / "tiki-rehearsal-lounge")
        if local_app:
            search_dirs.append(Path(local_app) / "Programs" / "Tiki Rehearsal Lounge")
            search_dirs.append(Path(local_app) / "Tiki Rehearsal Lounge")
        if app_data:
            search_dirs.append(Path(app_data) / "Tiki Rehearsal Lounge")
        search_dirs.append(user_home / "AppData" / "Local" / "Programs" / "Tiki Rehearsal Lounge")

        # Sibling directories relative to current app executable / bundle
        app_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else current_dir
        search_dirs.extend([
            app_root.parent / "Tiki Rehearsal Lounge",
            app_root.parent / "tiki-rehearsal-lounge",
            current_dir.parent / "Tiki Rehearsal Lounge",
            current_dir.parent / "tiki-rehearsal-lounge",
            app_root,
            app_root / "bin",
            current_dir,
            current_dir / "bin",
            current_dir.parent,
            current_dir.parent / "bin",
        ])

        for sdir in search_dirs:
            exe = sdir / "TikiRehearsalLounge.exe"
            if exe.is_file():
                return ([str(exe)], exe.parent)
    else:
        # Check PATH and system locations on Linux / macOS
        for bin_name in ["tiki-rehearsal-lounge", "TikiRehearsalLounge"]:
            which_path = shutil.which(bin_name)
            if which_path and os.access(which_path, os.X_OK):
                p = Path(which_path)
                return ([str(p)], p.parent)

        direct_bins = [
            current_dir / "TikiRehearsalLounge",
            current_dir / "bin" / "TikiRehearsalLounge",
            Path.home() / ".local" / "bin" / "tiki-rehearsal-lounge",
            Path("/usr/local/bin/tiki-rehearsal-lounge"),
            Path("/usr/bin/tiki-rehearsal-lounge"),
        ]
        for b in direct_bins:
            if b.is_file() and os.access(b, os.X_OK):
                return ([str(b)], b.parent)

        # Check AppImages in current, parent, and Applications directories
        appimage_dirs = [
            current_dir,
            current_dir.parent,
            Path.home() / "Applications",
            Path.home() / "Desktop",
            Path.home() / "Downloads",
        ]
        for search_dir in appimage_dirs:
            if not search_dir.is_dir():
                continue
            for pattern in ["*Rehearsal*.AppImage", "*rehearsal*.AppImage", "TikiRehearsalLounge*.AppImage"]:
                for appimage in sorted(search_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
                    if appimage.is_file() and os.access(appimage, os.X_OK):
                        return ([str(appimage)], appimage.parent)

    # 2. Check sibling repository: ../tiki-rehearsal-lounge
    rehearsal_repo = current_dir.parent / "tiki-rehearsal-lounge"
    if rehearsal_repo.is_dir():
        if sys.platform == "win32":
            win_exe = rehearsal_repo / "dist" / "windows" / "TikiRehearsalLounge.exe"
            if win_exe.is_file():
                return ([str(win_exe)], win_exe.parent)
        else:
            dist_dir = rehearsal_repo / "dist"
            if dist_dir.is_dir():
                for pattern in ["*Rehearsal*.AppImage", "*rehearsal*.AppImage", "TikiRehearsalLounge*.AppImage"]:
                    for appimage in sorted(dist_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
                        if appimage.is_file() and os.access(appimage, os.X_OK):
                            return ([str(appimage)], appimage.parent)

        main_py = rehearsal_repo / "main.py"
        if main_py.is_file():
            return ([sys.executable, str(main_py)], rehearsal_repo)

    return None


@router.post("/api/host/launch-importer")
async def api_launch_importer(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    cb = get_launch_importer_trigger()
    if cb:
        try:
            cb()
            return {
                "status": "ok",
                "message": "Tiki Karaoke Smart Importer opened successfully on desktop"
            }
        except Exception as e:
            print(f"[API] Error invoking importer desktop trigger: {e}")

    found = find_importer_executable()
    if not found:
        raise HTTPException(
            status_code=404,
            detail="Tiki Karaoke Smart Importer executable not found. Please ensure TikiKaraokeImporter is installed."
        )

    cmd_base, work_dir = found
    db_path = str(get_db_path().resolve()) if hasattr(get_db_path(), "resolve") else str(get_db_path())
    tracks_dir = str(get_tracks_dir().resolve()) if hasattr(get_tracks_dir(), "resolve") else str(get_tracks_dir())

    cmd = list(cmd_base)
    if "--importer" not in cmd and not any("importer" in str(x).lower() for x in cmd):
        cmd.append("--importer")
    cmd.extend(["--db", db_path, "--folder", tracks_dir])

    try:
        if sys.platform == "win32":
            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, creationflags=flags)
        else:
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, start_new_session=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch Smart Importer: {e}")

    return {
        "status": "ok",
        "message": "Tiki Karaoke Smart Importer launched successfully on desktop",
        "cmd": cmd[0]
    }


@router.post("/api/host/launch-cdg-ripper")
async def api_launch_cdg_ripper(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    cb_ripper = get_launch_cdg_ripper_trigger()
    if cb_ripper:
        try:
            cb_ripper()
            return {
                "status": "ok",
                "message": "CD+G Disc Ripper opened successfully on desktop"
            }
        except Exception as e:
            print(f"[API] Error invoking cdg ripper desktop trigger: {e}")

    found = find_importer_executable()
    if not found:
        raise HTTPException(
            status_code=404,
            detail="Tiki Karaoke Ripper/Importer executable not found."
        )

    cmd_base, work_dir = found
    tracks_dir = str(get_tracks_dir().resolve()) if hasattr(get_tracks_dir(), "resolve") else str(get_tracks_dir())

    clean_base = [a for a in cmd_base if a != "--importer"]
    cmd = clean_base + ["--cdg-ripper", "--folder", tracks_dir]

    try:
        if sys.platform == "win32":
            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, creationflags=flags)
        else:
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, start_new_session=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch CD+G Ripper: {e}")

    return {
        "status": "ok",
        "message": "CD+G Optical Disc Ripper launched successfully on desktop",
        "cmd": cmd[0]
    }


@router.post("/api/host/launch-rehearsal-lounge")
async def api_launch_rehearsal_lounge(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    found = find_rehearsal_lounge_executable()
    if not found:
        raise HTTPException(
            status_code=404,
            detail="Tiki Rehearsal Lounge executable not found."
        )

    cmd_base, work_dir = found
    cmd = list(cmd_base)

    try:
        if sys.platform == "win32":
            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, creationflags=flags)
        else:
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, start_new_session=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch Tiki Rehearsal Lounge: {e}")

    return {
        "status": "ok",
        "message": "Tiki Rehearsal Lounge launched successfully on desktop",
        "cmd": cmd[0]
    }
