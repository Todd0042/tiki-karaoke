import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("tiki.config")

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else APP_DIR
else:
    APP_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = APP_DIR

BASE_DIR = BUNDLE_DIR
STATIC_DIR = BUNDLE_DIR / "static"
TEMPLATES_DIR = BUNDLE_DIR / "templates"
APP_VERSION = os.getenv("TIKI_VERSION", "").strip() or "1.1.4"

def get_user_data_root() -> Path:
    """Returns safe persistent user data root across installed, portable, and AppImage modes."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            test_file = APP_DIR / ".write_test"
            test_file.touch()
            test_file.unlink()
            return APP_DIR
        except (PermissionError, OSError):
            appdata = os.getenv("APPDATA")
            if appdata:
                return Path(appdata) / "TikiKaraoke"
    elif getattr(sys, "frozen", False):
        # On Linux AppImage / PyInstaller: check if directory is writable (AppImage squashfs is read-only)
        try:
            test_file = APP_DIR / ".write_test"
            test_file.touch()
            test_file.unlink()
            return APP_DIR
        except (PermissionError, OSError):
            config_home = os.getenv("XDG_CONFIG_HOME")
            base = Path(config_home) / "TikiKaraoke" if config_home else Path.home() / ".config" / "TikiKaraoke"
            base.mkdir(parents=True, exist_ok=True)
            return base
    return APP_DIR

CONFIG_FILE = get_user_data_root() / "config.json"

def get_bundled_binary_path(binary_name: str) -> Path:
    """
    Returns the resolved Path to a bundled binary (e.g. cloudflared, bore, ffmpeg).
    Supports PyInstaller bundle directory (_MEIPASS), AppImage APPDIR, and Windows .exe extensions.
    """
    ext = ".exe" if sys.platform == "win32" else ""
    full_name = f"{binary_name}{ext}" if not binary_name.endswith(ext) else binary_name

    # 1. Check PyInstaller runtime temp dir if packaged (both _internal/bin and parent usr/bin/bin)
    if hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        for cand in (
            meipass / "bin" / full_name,
            meipass.parent / "bin" / full_name,
            meipass / full_name,
            meipass.parent / full_name,
        ):
            if cand.exists():
                return cand

    # 1b. Check AppImage APPDIR environment variable if set
    appdir_env = os.environ.get("APPDIR", "").strip()
    if appdir_env:
        appdir_path = Path(appdir_env)
        for cand in (
            appdir_path / "usr" / "bin" / "bin" / full_name,
            appdir_path / "usr" / "bin" / "_internal" / "bin" / full_name,
            appdir_path / "usr" / "bin" / full_name,
        ):
            if cand.exists():
                return cand

    # 2. Check project / application bin directory
    for cand in (
        APP_DIR / "bin" / full_name,
        APP_DIR / "_internal" / "bin" / full_name,
        APP_DIR / full_name,
        BASE_DIR / "bin" / full_name,
        BASE_DIR / "_internal" / "bin" / full_name,
    ):
        if cand.exists():
            return cand

    # 2b. Check staging packaging bin directory (including sibling windows repo)
    pkg_bin = BASE_DIR / "packaging" / "windows" / "bin" / full_name
    if pkg_bin.exists():
        return pkg_bin

    sibling_win_bin = BASE_DIR.parent / "tiki-karaoke-windows" / "bin" / full_name
    if sibling_win_bin.exists():
        return sibling_win_bin

    # 3. Fallback to system PATH lookup
    sys_which = shutil.which(full_name) or shutil.which(binary_name)
    if sys_which:
        return Path(sys_which)

    return BASE_DIR / "bin" / full_name


from hardware_detect import (
    _windows_mic_router_devices,
    find_system_browser,
    get_available_audio_sinks,
    get_available_audio_sources,
    get_available_browsers,
    get_available_displays,
    get_system_drives,
    open_url_in_preferred_browser,
)


DEFAULT_CONFIG = {
    "configured": False,
    "bar_name": "The Tiki Hut Karaoke Lounge",
    "host_pin": "1234",
    "require_room_code": False,
    "port": 8000,
    "remote_mode": "cloudflare_quick",
    "hotspot_enabled": False,
    "hotspot_ssid": "",
    "hotspot_password": "",
    "remote_port": 38480,
    "public_host": "",
    "cloudflare_token": "",
    "custom_domain": "",
    "tailscale_domain": "",
    "duckdns_domain": "",
    "duckdns_token": "",
    "target_lufs": -16.0,
    "intermission_seconds": 15,
    "tts_enabled": False,
    "bgm_enabled": True,
    "bgm_volume": 0.25,
    "google_client_id": "",
    "google_client_secret": "",
    "library_dir": "",
    "data_dir": "",
    "recordings_dir": "",
    "stage_display": 0,
    "stage_display_name": "Primary Display",
    "stage_display_x": 0,
    "stage_display_y": 0,
    "stage_display_width": 1920,
    "stage_display_height": 1080,
    "stage_display_enabled": True,
    "kiosk_mode": False,
    "preferred_browser": "auto",
    "audio_output_device": "default",
    "audio_output_name": "System Default Audio Output",
    "audio_input_device": "default",
    "audio_input_name": "System Default Microphone / Input",
    "host_display_enabled": False,
    "host_display": 1,
    "host_display_name": "Secondary Display",
    "host_display_x": 0,
    "host_display_y": 0,
    "host_display_width": 1920,
    "host_display_height": 1080,
    "host_kiosk_mode": False,
    "video_quality_profile": "pro",
    "marquee_scroll_speed": 85,
    "mic_monitoring_mode": "native"
}

def get_config_path() -> Path:
    root = get_user_data_root()
    cfg_file = root / "config.json"
    if cfg_file.exists():
        return cfg_file
    data_cfg = root / "data" / "config.json"
    if data_cfg.exists():
        return data_cfg
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    return cfg_file

def load_config() -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    cfg_path = get_config_path()
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    loaded = json.loads(content)
                    cfg.update(loaded)
        except Exception as e:
            print(f"[Config] Error reading {cfg_path}: {e}")
    return cfg

def get_default_library_dir() -> Path:
    return (Path.home() / "Music" / "Karaoke" / "Tiki").resolve()

def get_default_data_dir() -> Path:
    return (Path.home() / "Music" / "Karaoke" / "Tiki" / "db").resolve()

def get_library_dir() -> Path:
    cfg = load_config()
    p = os.getenv("TIKI_LIBRARY_DIR", "").strip() or cfg.get("library_dir", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return get_default_library_dir()

def get_tracks_dir() -> Path:
    return get_library_dir() / "tracks"

def get_playlists_dir() -> Path:
    return get_library_dir() / "playlists_m3u"

def get_filler_dir() -> Path:
    return get_library_dir() / "filler"

def get_sing_along_dir() -> Path:
    return get_library_dir() / "sing_along"

def get_default_recordings_dir() -> Path:
    return (get_library_dir() / "performance_recordings").resolve()

def get_recordings_dir() -> Path:
    cfg = load_config()
    p = os.getenv("TIKI_RECORDINGS_DIR", "").strip() or cfg.get("recordings_dir", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return get_default_recordings_dir()

def get_data_dir() -> Path:
    cfg = load_config()
    p = os.getenv("TIKI_DATA_DIR", "").strip() or cfg.get("data_dir", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return get_default_data_dir()

def get_db_path() -> Path:
    return get_data_dir() / "karaoke.db"

def get_secret_file() -> Path:
    return get_data_dir() / "host_token.txt"

def ensure_storage_dirs():
    try:
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        get_tracks_dir().mkdir(parents=True, exist_ok=True)
        get_playlists_dir().mkdir(parents=True, exist_ok=True)
        get_filler_dir().mkdir(parents=True, exist_ok=True)
        get_sing_along_dir().mkdir(parents=True, exist_ok=True)
        rec_dir = get_recordings_dir()
        rec_dir.mkdir(parents=True, exist_ok=True)

        # Legacy recordings directory migration safeguard:
        legacy_rec_dir = (get_library_dir() / "recordings").resolve()
        if legacy_rec_dir.exists() and legacy_rec_dir.is_dir() and legacy_rec_dir != rec_dir:
            try:
                for item in legacy_rec_dir.iterdir():
                    target_item = rec_dir / item.name
                    if not target_item.exists():
                        shutil.move(str(item), str(target_item))
                if not any(legacy_rec_dir.iterdir()):
                    legacy_rec_dir.rmdir()
            except Exception as e:
                print(f"[Storage] Notice: Legacy recordings migration note: {e}")

        # Legacy data migration safeguard:
        legacy_data = (get_user_data_root() / "data").resolve()
        if legacy_data != data_dir:
            legacy_db = legacy_data / "karaoke.db"
            target_db = data_dir / "karaoke.db"
            if legacy_db.exists() and not target_db.exists():
                try:
                    shutil.copy2(legacy_db, target_db)
                    print(f"[Config] Seamlessly migrated legacy database from {legacy_db} -> {target_db}")
                except Exception as mig_err:
                    print(f"[Config] Note: Could not copy legacy database: {mig_err}")

            legacy_token = legacy_data / "host_token.txt"
            target_token = data_dir / "host_token.txt"
            if legacy_token.exists() and not target_token.exists():
                try:
                    shutil.copy2(legacy_token, target_token)
                except OSError as e:
                    logger.warning(f"[Config] Could not migrate legacy host_token.txt: {e}")
    except Exception as e:
        print(f"[Config] Warning: could not ensure storage directories: {e}")

# Initialize directories
ensure_storage_dirs()

def get_host_token() -> str:
    sec_file = get_secret_file()
    if sec_file.exists():
        return sec_file.read_text().strip()
    # Check fallback legacy path
    legacy = BASE_DIR / "data" / "host_token.txt"
    if legacy.exists() and legacy != sec_file:
        token = legacy.read_text().strip()
        try:
            sec_file.parent.mkdir(parents=True, exist_ok=True)
            sec_file.write_text(token)
            return token
        except OSError as e:
            logger.warning(f"[Config] Could not persist migrated host token to {sec_file}: {e}")
            return token

    token = secrets.token_hex(16)
    try:
        sec_file.parent.mkdir(parents=True, exist_ok=True)
        sec_file.write_text(token)
    except OSError as e:
        logger.warning(f"[Config] Could not persist new host token to {sec_file}: {e}")
    return token

def refresh_globals():
    global DATA_DIR, LIBRARY_DIR, TRACKS_DIR, PLAYLISTS_DIR, DB_PATH, SECRET_FILE, HOST_TOKEN
    global HOST_PIN, PORT, PUBLIC_HOST, TARGET_LUFS, DEFAULT_INTERMISSION_SECONDS, REMOTE_MODE, REMOTE_PORT, BAR_NAME, VIDEO_QUALITY_PROFILE
    global CLOUDFLARE_TOKEN, CUSTOM_DOMAIN, TAILSCALE_DOMAIN, DUCKDNS_DOMAIN, DUCKDNS_TOKEN
    _current_cfg = load_config()
    DATA_DIR = get_data_dir()
    LIBRARY_DIR = get_library_dir()
    TRACKS_DIR = get_tracks_dir()
    PLAYLISTS_DIR = get_playlists_dir()
    DB_PATH = get_db_path()
    SECRET_FILE = get_secret_file()
    HOST_TOKEN = get_host_token()

    HOST_PIN = os.getenv("TIKI_HOST_PIN", _current_cfg.get("host_pin", "1234"))
    PORT = int(os.getenv("TIKI_PORT", str(_current_cfg.get("port", 8000))))
    PUBLIC_HOST = os.getenv("TIKI_PUBLIC_HOST", _current_cfg.get("public_host", "")).strip()
    TARGET_LUFS = float(_current_cfg.get("target_lufs", -16.0))
    DEFAULT_INTERMISSION_SECONDS = int(_current_cfg.get("intermission_seconds", 15))
    REMOTE_MODE = os.getenv("TIKI_REMOTE_MODE", _current_cfg.get("remote_mode", "cloudflare_quick"))
    REMOTE_PORT = int(os.getenv("TIKI_REMOTE_PORT", str(_current_cfg.get("remote_port", 38480))))
    BAR_NAME = os.getenv("TIKI_BAR_NAME", _current_cfg.get("bar_name", "The Tiki Hut Karaoke Lounge"))
    VIDEO_QUALITY_PROFILE = _current_cfg.get("video_quality_profile", "pro")
    CLOUDFLARE_TOKEN = os.getenv("TIKI_CLOUDFLARE_TOKEN", _current_cfg.get("cloudflare_token", "")).strip()
    CUSTOM_DOMAIN = os.getenv("TIKI_CUSTOM_DOMAIN", _current_cfg.get("custom_domain", "")).strip()
    TAILSCALE_DOMAIN = os.getenv("TIKI_TAILSCALE_DOMAIN", _current_cfg.get("tailscale_domain", "")).strip()
    DUCKDNS_DOMAIN = os.getenv("TIKI_DUCKDNS_DOMAIN", _current_cfg.get("duckdns_domain", "")).strip()
    DUCKDNS_TOKEN = os.getenv("TIKI_DUCKDNS_TOKEN", _current_cfg.get("duckdns_token", "")).strip()

def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config()
    cfg.update(updates)
    cfg["configured"] = True
    cfg_path = get_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    ensure_storage_dirs()
    refresh_globals()
    if "video_quality_profile" in updates:
        try:
            from database import set_setting
            set_setting("video_quality_profile", str(updates["video_quality_profile"]))
        except Exception as e:
            logger.warning(f"[Config] Could not sync video_quality_profile to database: {e}")
    return cfg

def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg.get("configured", False))

def get_path_info(path_str: str) -> Dict[str, Any]:
    """Inspects a path for valid write permissions, existence, and free disk space."""
    import shutil
    try:
        p = Path(path_str).expanduser().resolve()
        probe = p
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        usage = shutil.disk_usage(probe)
        free_gb = round(usage.free / (1024**3), 2)
        total_gb = round(usage.total / (1024**3), 2)
        used_gb = round(usage.used / (1024**3), 2)
        writable = os.access(probe, os.W_OK)
        return {
            "valid": True,
            "path": str(p),
            "exists": p.exists(),
            "writable": writable,
            "free_gb": free_gb,
            "total_gb": total_gb,
            "used_gb": used_gb
        }
    except Exception as e:
        return {
            "valid": False,
            "error": str(e)
        }

def get_storage_status() -> Dict[str, Any]:
    """Returns disk usage, percentage, status color (green/orange/red), and local library stats."""
    import shutil
    tracks_dir = get_tracks_dir()
    probe = tracks_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent

    usage = shutil.disk_usage(probe)
    total_gb = round(usage.total / (1024**3), 1)
    used_gb = round(usage.used / (1024**3), 1)
    free_gb = round(usage.free / (1024**3), 1)
    used_pct = round((usage.used / usage.total) * 100, 1)

    lib_bytes = 0
    track_count = 0
    dirs_to_scan = [tracks_dir, get_sing_along_dir()]
    for d in dirs_to_scan:
        if d.exists():
            for f in d.glob("**/*"):
                if f.is_file():
                    try:
                        lib_bytes += f.stat().st_size
                        if f.suffix.lower() == ".mp4":
                            track_count += 1
                    except OSError as e:
                        logger.debug(f"[Storage] Could not stat library file {f}: {e}")
    lib_mb = round(lib_bytes / (1024**2), 1)
    lib_gb = round(lib_bytes / (1024**3), 2)

    # Live singer performance recordings metrics
    rec_dir = get_recordings_dir()
    rec_bytes = 0
    rec_count = 0
    singer_dirs = set()
    if rec_dir.exists():
        try:
            for f in rec_dir.glob("**/*"):
                if f.is_file() and f.suffix.lower() in (".mp3", ".webm", ".wav", ".m4a"):
                    rec_bytes += f.stat().st_size
                    rec_count += 1
                    singer_dirs.add(f.parent.name)
        except OSError as e:
            logger.warning(f"[Storage] Could not scan recordings dir {rec_dir}: {e}")
    rec_mb = round(rec_bytes / (1024**2), 1)

    # Color thresholds: green default, orange at >= 75%, red at >= 90%
    if used_pct >= 90.0:
        status_color = "red"
        status_level = "critical"
        message = f"🚨 Storage Critical: {used_pct}% Full ({free_gb} GB remaining). Pruning recommended!"
    elif used_pct >= 75.0:
        status_color = "orange"
        status_level = "warning"
        message = f"⚠️ Storage Warning: {used_pct}% Full ({free_gb} GB remaining)."
    else:
        status_color = "green"
        status_level = "normal"
        message = f"🌴 Storage Healthy: {used_pct}% Full ({free_gb} GB remaining)."

    return {
        "partition_path": str(probe),
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "used_percent": used_pct,
        "status_color": status_color,
        "status_level": status_level,
        "message": message,
        "library_bytes": lib_bytes,
        "library_mb": lib_mb,
        "library_gb": lib_gb,
        "downloaded_track_count": track_count,
        "recordings_dir": str(rec_dir),
        "recordings_bytes": rec_bytes,
        "recordings_mb": rec_mb,
        "recordings_count": rec_count,
        "recordings_singers_count": len(singer_dirs)
    }

# Initial export of globals
DATA_DIR = get_data_dir()
LIBRARY_DIR = get_library_dir()
TRACKS_DIR = get_tracks_dir()
PLAYLISTS_DIR = get_playlists_dir()
SING_ALONG_DIR = get_sing_along_dir()
RECORDINGS_DIR = get_recordings_dir()
DB_PATH = get_db_path()
SECRET_FILE = get_secret_file()
HOST_TOKEN = get_host_token()

_current_cfg = load_config()
HOST_PIN = os.getenv("TIKI_HOST_PIN", _current_cfg.get("host_pin", "1234"))
PORT = int(os.getenv("TIKI_PORT", str(_current_cfg.get("port", 8000))))
PUBLIC_HOST = os.getenv("TIKI_PUBLIC_HOST", _current_cfg.get("public_host", "")).strip()
TARGET_LUFS = float(_current_cfg.get("target_lufs", -16.0))
DEFAULT_INTERMISSION_SECONDS = int(_current_cfg.get("intermission_seconds", 15))
REMOTE_MODE = os.getenv("TIKI_REMOTE_MODE", _current_cfg.get("remote_mode", "cloudflare_quick"))
REMOTE_PORT = int(os.getenv("TIKI_REMOTE_PORT", str(_current_cfg.get("remote_port", 38480))))
BAR_NAME = os.getenv("TIKI_BAR_NAME", _current_cfg.get("bar_name", "The Tiki Hut Karaoke Lounge"))
VIDEO_QUALITY_PROFILE = _current_cfg.get("video_quality_profile", "pro")
