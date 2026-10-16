import os
import json
import secrets
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LIBRARY_DIR = BASE_DIR / "karaoke_library"
TRACKS_DIR = LIBRARY_DIR / "tracks"
PLAYLISTS_DIR = LIBRARY_DIR / "playlists_m3u"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
CONFIG_FILE = BASE_DIR / "config.json"
DATA_CONFIG_FILE = DATA_DIR / "config.json"

# Ensure runtime directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
TRACKS_DIR.mkdir(parents=True, exist_ok=True)
PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "karaoke.db"
SECRET_FILE = DATA_DIR / "host_token.txt"

# Host authentication token (persisted across restarts)
if SECRET_FILE.exists():
    HOST_TOKEN = SECRET_FILE.read_text().strip()
else:
    HOST_TOKEN = secrets.token_hex(16)
    SECRET_FILE.write_text(HOST_TOKEN)

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
    "google_client_secret": ""
}

def get_config_path() -> Path:
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    if DATA_CONFIG_FILE.exists():
        return DATA_CONFIG_FILE
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

def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config()
    cfg.update(updates)
    cfg["configured"] = True
    cfg_path = CONFIG_FILE
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg

def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg.get("configured", False))

_current_cfg = load_config()
HOST_PIN = os.getenv("TIKI_HOST_PIN", _current_cfg.get("host_pin", "1234"))
PORT = int(os.getenv("TIKI_PORT", str(_current_cfg.get("port", 8000))))
PUBLIC_HOST = os.getenv("TIKI_PUBLIC_HOST", _current_cfg.get("public_host", "")).strip()
TARGET_LUFS = float(_current_cfg.get("target_lufs", -16.0))
DEFAULT_INTERMISSION_SECONDS = int(_current_cfg.get("intermission_seconds", 20))
REMOTE_MODE = os.getenv("TIKI_REMOTE_MODE", _current_cfg.get("remote_mode", "bore"))
REMOTE_PORT = int(os.getenv("TIKI_REMOTE_PORT", str(_current_cfg.get("remote_port", 38480))))
BAR_NAME = os.getenv("TIKI_BAR_NAME", _current_cfg.get("bar_name", "The Tiki Hut Karaoke Lounge"))

