import os
import json
import secrets
from pathlib import Path
from typing import Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "configured": False,
    "bar_name": "The Tiki Hut Karaoke Lounge",
    "host_pin": "1234",
    "port": 8000,
    "remote_mode": "bore",
    "remote_port": 38480,
    "public_host": "",
    "target_lufs": -16.0,
    "intermission_seconds": 20,
    "tts_enabled": False,
    "bgm_enabled": True,
    "bgm_volume": 0.25,
    "google_client_id": "",
    "google_client_secret": "",
    "library_dir": "",
    "data_dir": ""
}

def get_config_path() -> Path:
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    data_cfg = BASE_DIR / "data" / "config.json"
    if data_cfg.exists():
        return data_cfg
    return CONFIG_FILE

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

def get_library_dir() -> Path:
    cfg = load_config()
    p = os.getenv("TIKI_LIBRARY_DIR", "").strip() or cfg.get("library_dir", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return (BASE_DIR / "karaoke_library").resolve()

def get_tracks_dir() -> Path:
    return get_library_dir() / "tracks"

def get_playlists_dir() -> Path:
    return get_library_dir() / "playlists_m3u"

def get_data_dir() -> Path:
    cfg = load_config()
    p = os.getenv("TIKI_DATA_DIR", "").strip() or cfg.get("data_dir", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return (BASE_DIR / "data").resolve()

def get_db_path() -> Path:
    return get_data_dir() / "karaoke.db"

def get_secret_file() -> Path:
    return get_data_dir() / "host_token.txt"

def ensure_storage_dirs():
    try:
        get_data_dir().mkdir(parents=True, exist_ok=True)
        get_tracks_dir().mkdir(parents=True, exist_ok=True)
        get_playlists_dir().mkdir(parents=True, exist_ok=True)
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
        except Exception:
            return token

    token = secrets.token_hex(16)
    try:
        sec_file.parent.mkdir(parents=True, exist_ok=True)
        sec_file.write_text(token)
    except Exception:
        pass
    return token

def refresh_globals():
    global DATA_DIR, LIBRARY_DIR, TRACKS_DIR, PLAYLISTS_DIR, DB_PATH, SECRET_FILE, HOST_TOKEN
    global HOST_PIN, PORT, PUBLIC_HOST, TARGET_LUFS, DEFAULT_INTERMISSION_SECONDS, REMOTE_MODE, REMOTE_PORT, BAR_NAME
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
    DEFAULT_INTERMISSION_SECONDS = int(_current_cfg.get("intermission_seconds", 20))
    REMOTE_MODE = os.getenv("TIKI_REMOTE_MODE", _current_cfg.get("remote_mode", "bore"))
    REMOTE_PORT = int(os.getenv("TIKI_REMOTE_PORT", str(_current_cfg.get("remote_port", 38480))))
    BAR_NAME = os.getenv("TIKI_BAR_NAME", _current_cfg.get("bar_name", "The Tiki Hut Karaoke Lounge"))

def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config()
    cfg.update(updates)
    cfg["configured"] = True
    cfg_path = CONFIG_FILE
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    ensure_storage_dirs()
    refresh_globals()
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
    if tracks_dir.exists():
        for f in tracks_dir.glob("**/*"):
            if f.is_file():
                try:
                    lib_bytes += f.stat().st_size
                    if f.suffix.lower() == ".mp4":
                        track_count += 1
                except Exception:
                    pass
    lib_mb = round(lib_bytes / (1024**2), 1)
    lib_gb = round(lib_bytes / (1024**3), 2)

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
        "downloaded_track_count": track_count
    }

# Initial export of globals
DATA_DIR = get_data_dir()
LIBRARY_DIR = get_library_dir()
TRACKS_DIR = get_tracks_dir()
PLAYLISTS_DIR = get_playlists_dir()
DB_PATH = get_db_path()
SECRET_FILE = get_secret_file()
HOST_TOKEN = get_host_token()

_current_cfg = load_config()
HOST_PIN = os.getenv("TIKI_HOST_PIN", _current_cfg.get("host_pin", "1234"))
PORT = int(os.getenv("TIKI_PORT", str(_current_cfg.get("port", 8000))))
PUBLIC_HOST = os.getenv("TIKI_PUBLIC_HOST", _current_cfg.get("public_host", "")).strip()
TARGET_LUFS = float(_current_cfg.get("target_lufs", -16.0))
DEFAULT_INTERMISSION_SECONDS = int(_current_cfg.get("intermission_seconds", 20))
REMOTE_MODE = os.getenv("TIKI_REMOTE_MODE", _current_cfg.get("remote_mode", "bore"))
REMOTE_PORT = int(os.getenv("TIKI_REMOTE_PORT", str(_current_cfg.get("remote_port", 38480))))
BAR_NAME = os.getenv("TIKI_BAR_NAME", _current_cfg.get("bar_name", "The Tiki Hut Karaoke Lounge"))
