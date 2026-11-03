import os
import sys
from pathlib import Path
from typing import Optional, Tuple


def find_tiki_karaoke_repo() -> Optional[Path]:
    """Attempts to locate the sibling tiki-karaoke repository."""
    current_dir = Path(__file__).resolve().parent
    candidates = [
        current_dir.parent.parent / "tiki-karaoke",
        Path.home() / "Documents" / "GitHub" / "tiki-karaoke",
        Path.home() / "tiki-karaoke",
    ]
    for c in candidates:
        if c.exists() and (c / "config.py").exists():
            return c
    return None


def get_default_db_path() -> str:
    """Finds the default karaoke.db path from Tiki Karaoke config or standard OS locations."""
    # 1. Try importing from sibling tiki-karaoke repo if available
    repo = find_tiki_karaoke_repo()
    if repo:
        try:
            sys.path.insert(0, str(repo))
            import config as tiki_cfg
            if hasattr(tiki_cfg, "DB_PATH") and Path(tiki_cfg.DB_PATH).parent.exists():
                return str(Path(tiki_cfg.DB_PATH).resolve())
        except Exception:
            pass
        finally:
            if repo and str(repo) in sys.path:
                sys.path.remove(str(repo))

    # 2. Check standard Linux / Windows user data locations
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            p = Path(appdata) / "TikiKaraoke" / "data" / "karaoke.db"
            if p.parent.exists():
                return str(p.resolve())
        doc_p = Path.home() / "Documents" / "tiki-karaoke" / "data" / "karaoke.db"
        if doc_p.parent.exists():
            return str(doc_p.resolve())
    else:
        doc_p = Path.home() / "Documents" / "tiki-karaoke" / "data" / "karaoke.db"
        if doc_p.parent.exists():
            return str(doc_p.resolve())
        dot_p = Path.home() / ".local" / "share" / "tiki-karaoke" / "karaoke.db"
        if dot_p.parent.exists():
            return str(dot_p.resolve())

    # Fallback default
    return str(Path.home() / "Documents" / "tiki-karaoke" / "data" / "karaoke.db")


def get_default_library_path() -> str:
    """Finds the default tracks directory from Tiki Karaoke config or OS storage."""
    repo = find_tiki_karaoke_repo()
    if repo:
        try:
            sys.path.insert(0, str(repo))
            import config as tiki_cfg
            if hasattr(tiki_cfg, "TRACKS_DIR") and Path(tiki_cfg.TRACKS_DIR).exists():
                return str(Path(tiki_cfg.TRACKS_DIR).resolve())
        except Exception:
            pass
        finally:
            if repo and str(repo) in sys.path:
                sys.path.remove(str(repo))

    # Check common media directories
    candidates = [
        Path.home() / "storage" / "Karaoke" / "tracks",
        Path.home() / "storage" / "Karaoke",
        Path.home() / "Documents" / "tiki-karaoke" / "data" / "tracks",
        Path.home() / "Music" / "Karaoke",
        Path.home() / "Videos" / "Karaoke",
    ]
    for c in candidates:
        if c.exists():
            return str(c.resolve())

    return str(Path.home() / "storage" / "Karaoke" / "tracks")


def get_default_compuhost_path() -> str:
    """
    Automatically detects CompuHost database files (SongList.tps or .ciqbackup)
    across all standard Windows installation directories (including versioned folders
    like 'CompuHost V4', 'CompuHost V3', etc.), ProgramData, AppData, and user documents.
    """
    candidates = []
    parent_dirs = []

    if sys.platform == "win32":
        env_pfx86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        env_pf = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        env_program_data = os.environ.get("PROGRAMDATA", "C:\\ProgramData")
        env_userprofile = os.environ.get("USERPROFILE")
        env_appdata = os.environ.get("APPDATA")
        env_localappdata = os.environ.get("LOCALAPPDATA")

        # Standard Windows program folders first (highest priority)
        if env_pfx86:
            parent_dirs.append(Path(env_pfx86))
        if env_pf:
            parent_dirs.append(Path(env_pf))
        for drive in ["C:", "D:", "E:", "F:"]:
            parent_dirs.append(Path(f"{drive}\\"))
        if env_program_data:
            parent_dirs.append(Path(env_program_data))
        if env_userprofile:
            parent_dirs.append(Path(env_userprofile) / "Documents")
        if env_appdata:
            parent_dirs.append(Path(env_appdata))
        if env_localappdata:
            parent_dirs.append(Path(env_localappdata))
    else:
        # Linux / Wine compatibility paths
        wine_prefix = os.environ.get("WINEPREFIX", str(Path.home() / ".wine"))
        wine_c = Path(wine_prefix) / "drive_c"
        if wine_c.exists():
            parent_dirs.extend([
                wine_c / "Program Files (x86)",
                wine_c / "Program Files",
                wine_c,
                wine_c / "ProgramData",
                wine_c / "users" / os.environ.get("USER", "todd") / "Documents",
            ])

    base_folders = []
    # Prioritized common CompuHost versioned names (CompuHost V4 is standard!)
    version_names = [
        "CompuHost V4",
        "CompuHost V5",
        "CompuHost V3",
        "CompuHost V2",
        "CompuHost",
        "Compu-Host",
    ]

    for parent in parent_dirs:
        if not parent:
            continue
        try:
            if not parent.is_dir():
                continue
        except Exception:
            continue

        # Check known version names
        for vname in version_names:
            vpath = parent / vname
            if vpath not in base_folders:
                try:
                    if vpath.is_dir():
                        base_folders.append(vpath)
                except Exception:
                    pass

        # Dynamic search for any folder starting with compuhost
        try:
            for item in parent.iterdir():
                if item not in base_folders:
                    if item.name.lower().startswith("compuhost"):
                        if item.is_dir():
                            base_folders.append(item)
        except Exception:
            pass

    for base in base_folders:
        # Top-level install directory (e.g. C:\Program Files (x86)\CompuHost V4\SongList.tps)
        candidates.append(base / "SongList.tps")
        # Subdirectories
        candidates.append(base / "Data" / "SongList.tps")
        candidates.append(base / "Database" / "SongList.tps")
        candidates.append(base / "DB" / "SongList.tps")

        # Backups (.ciqbackup)
        backup_dir = base / "Backups"
        try:
            if backup_dir.is_dir():
                backups = sorted(backup_dir.glob("*.ciqbackup"), key=lambda p: p.stat().st_mtime, reverse=True)
                candidates.extend(backups)
        except Exception:
            pass

        ciq_dir = base / "Data" / "CatalogIQ"
        try:
            if ciq_dir.is_dir():
                backups = sorted(ciq_dir.glob("*.ciqbackup"), key=lambda p: p.stat().st_mtime, reverse=True)
                candidates.extend(backups)
        except Exception:
            pass

    # Relative / local workspace fallback (for test suites or bundled fixtures)
    cwd = Path.cwd()
    candidates.extend([
        cwd / "compuhost" / "SongList.tps",
        cwd / "SongList.tps",
    ])

    # Check candidates in order
    for c in candidates:
        try:
            if c and c.is_file():
                return str(c.resolve())
        except Exception:
            continue

    return ""
