#!/usr/bin/env bash
"true" '''\'
exec "$(dirname "$0")/venv/bin/python" "$0" "$@"
'''
# -*- coding: utf-8 -*-
import os
import sys
import shutil
import json
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import (
    CONFIG_FILE, load_config, save_config, is_configured, DEFAULT_CONFIG,
    get_library_dir, get_tracks_dir, get_playlists_dir, get_data_dir, get_db_path,
    get_path_info, ensure_storage_dirs
)
from database import init_db, set_setting, migrate_storage_paths

TIKI_SETUP_BANNER = r"""
  ______ _            _     _         _______ _    _ 
 |  ____| |          (_)   | |       |__   __(_)  | |
 | |__  | | ___  _ __ _  __| | __ _     | |   _   | | _____  
 |  __| | |/ _ \| '__| |/ _` |/ _` |    | |  | |  | |/ / _ \ 
 | |    | | (_) | |  | | (_| | (_| |    | |  | |  |   <  __/ 
 |_|    |_|\___/|_|  |_|\__,_|\__,_|    |_|  |_|  |_|\_\___| 
      🌴 🌺  INITIAL SETUP & CONFIGURATION WIZARD  🍹 🌴
"""

def prompt(text: str, default: str = "") -> str:
    prompt_str = f"\033[1;36m{text}\033[0m"
    if default:
        prompt_str += f" [\033[1;33m{default}\033[0m]"
    prompt_str += ": "
    try:
        val = input(prompt_str).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[Setup] Cancelled by user.")
        sys.exit(1)
    return val if val else default

def check_dependencies():
    print("\n🔍 Checking system dependencies...")
    ok = True

    # 1. FFmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        print(f"  ✅ FFmpeg found: {ffmpeg_path}")
    else:
        print("  ❌ FFmpeg NOT found! Please install ffmpeg via: sudo apt-get install -y ffmpeg")
        ok = False

    # 2. FFprobe
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        print(f"  ✅ FFprobe found: {ffprobe_path}")
    else:
        print("  ❌ FFprobe NOT found! Please install ffmpeg via: sudo apt-get install -y ffmpeg")
        ok = False

    # 3. Tunnel binaries
    bore_path = BASE_DIR / "bin" / "bore"
    if bore_path.exists():
        print(f"  ✅ Bore binary found: {bore_path}")
    else:
        print(f"  ⚠️ Bore binary missing in bin/bore (Remote static tunnel mode may need compilation)")

    cloudflared_path = BASE_DIR / "bin" / "cloudflared"
    if cloudflared_path.exists():
        print(f"  ✅ Cloudflared binary found: {cloudflared_path}")
    else:
        print(f"  ℹ️ Cloudflared binary missing (Cloudflare tunnel mode will not be available)")

    return ok

def run_setup():
    print(TIKI_SETUP_BANNER)
    print("Welcome! Let's set up your Florida Tiki Karaoke Lounge.")
    print("This wizard will populate your config.json file and initialize the system.\n")

    check_dependencies()

    current = load_config()

    print("\n👑 1. LOUNGE IDENTITY & HOST SECURITY")
    bar_name = prompt("Bar / Venue Name", current.get("bar_name") or "The Tiki Hut Karaoke Lounge")
    host_pin = prompt("Host DJ Master Remote PIN (4 digits)", current.get("host_pin") or "1234")
    port_str = prompt("Local Web Server Port", str(current.get("port") or 8000))
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    print("\n🌐 2. REMOTE CONNECTIVITY & URLS")
    print("  [1] Fixed Static URL (bore.pub:38480) - Recommended: permanent, zero router config")
    print("  [2] Cloudflare HTTPS Tunnel - Ephemeral secure HTTPS domain")
    print("  [3] Local Wi-Fi Network Only")
    print("  [4] Custom Host / IP")
    
    choice = prompt("Select Remote Mode (1-4)", "1")
    remote_mode = "bore"
    public_host = ""
    remote_port = 38480

    if choice == "2":
        remote_mode = "cloudflare"
    elif choice == "3":
        remote_mode = "local"
    elif choice == "4":
        remote_mode = "custom"
        public_host = prompt("Enter your public domain/IP (e.g. karaoke.example.com:8000)")
    else:
        remote_mode = "bore"
        remote_port_str = prompt("Permanent Port on bore.pub", str(current.get("remote_port") or 38480))
        try:
            remote_port = int(remote_port_str)
        except ValueError:
            remote_port = 38480

    print("\n🔊 3. AUDIO & STAGE EXPERIENCE")
    lufs_str = prompt("Audio Loudness Target LUFS (EBU R128)", str(current.get("target_lufs") or -16.0))
    try:
        target_lufs = float(lufs_str)
    except ValueError:
        target_lufs = -16.0

    intermission_str = prompt("Intermission Countdown Seconds between songs", str(current.get("intermission_seconds") or 20))
    try:
        intermission_seconds = int(intermission_str)
    except ValueError:
        intermission_seconds = 20

    bgm_choice = prompt("Enable Tropical Background Intermission Music (BGM)? [Y/n]", "y")
    bgm_enabled = bgm_choice.lower().startswith("y")

    tts_choice = prompt("Enable Virtual DJ TTS Voice Announcer? [y/N]", "n")
    tts_enabled = tts_choice.lower().startswith("y")

    print("\n💾 4. STORAGE & MEDIA DIRECTORIES")
    print("Choose where downloaded karaoke video files, playlists, and database are saved.")
    print("Ideal for secondary high-speed NVMe SSDs or dedicated storage drives (e.g. /home/todd/storage/karaoke).")
    
    current_lib = current.get("library_dir") or str(get_library_dir())
    current_data = current.get("data_dir") or str(get_data_dir())

    lib_input = prompt("Video Media Library Path (for MP4s & Playlists)", current_lib)
    lib_path = Path(lib_input).expanduser().resolve()
    info_lib = get_path_info(str(lib_path))
    if info_lib.get("valid"):
        print(f"  ✅ Drive verified: \033[1;32m{info_lib['free_gb']} GB\033[0m free on {info_lib['path']}")
    else:
        print(f"  ⚠️ Warning on library path: {info_lib.get('error', 'Unverified')}")

    data_input = prompt("Database & Runtime Data Path (for karaoke.db)", current_data)
    data_path = Path(data_input).expanduser().resolve()
    info_data = get_path_info(str(data_path))
    if info_data.get("valid"):
        print(f"  ✅ Drive verified: \033[1;32m{info_data['free_gb']} GB\033[0m free on {info_data['path']}")
    else:
        print(f"  ⚠️ Warning on data path: {info_data.get('error', 'Unverified')}")

    # Check for migration of existing library tracks if path changed
    old_tracks = get_tracks_dir()
    new_tracks = lib_path / "tracks"
    if old_tracks.exists() and old_tracks.resolve() != new_tracks.resolve():
        existing_tracks = list(old_tracks.glob("*/*.mp4"))
        if existing_tracks:
            print(f"\n📦 Detected {len(existing_tracks)} downloaded video files in previous library location ({old_tracks}).")
            do_migrate = prompt("Migrate existing video files to new location? [Y/n]", "y")
            if do_migrate.lower().startswith("y"):
                print("  ⏳ Moving video files...")
                new_tracks.mkdir(parents=True, exist_ok=True)
                for artist_dir in old_tracks.iterdir():
                    if artist_dir.is_dir() and artist_dir.name != "tracks":
                        target_artist = new_tracks / artist_dir.name
                        target_artist.mkdir(parents=True, exist_ok=True)
                        for f in artist_dir.iterdir():
                            dest_f = target_artist / f.name
                            if not dest_f.exists():
                                shutil.move(str(f), str(dest_f))
                # Update sqlite records
                updated_count = migrate_storage_paths(str(old_tracks), str(new_tracks))
                print(f"  ✅ {len(existing_tracks)} files moved and {updated_count} database paths updated!")

    # Check for migration of existing database if data_dir changed
    old_db = get_db_path()
    new_db = data_path / "karaoke.db"
    if old_db.exists() and old_db.resolve() != new_db.resolve() and not new_db.exists():
        data_path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(old_db), str(new_db))
        print("  ✅ Copied existing karaoke.db to new data directory!")

    # Build updated config
    config_data = {
        "configured": True,
        "bar_name": bar_name,
        "host_pin": host_pin,
        "port": port,
        "remote_mode": remote_mode,
        "remote_port": remote_port,
        "public_host": public_host,
        "target_lufs": target_lufs,
        "intermission_seconds": intermission_seconds,
        "tts_enabled": tts_enabled,
        "bgm_enabled": bgm_enabled,
        "bgm_volume": 0.25,
        "google_client_id": current.get("google_client_id", ""),
        "google_client_secret": current.get("google_client_secret", ""),
        "library_dir": str(lib_path),
        "data_dir": str(data_path)
    }

    print("\n💾 Writing configuration to config.json...")
    save_config(config_data)

    print("📁 Initializing database schema and default settings...")
    init_db()
    set_setting("bar_name", bar_name)
    set_setting("intermission_seconds", str(intermission_seconds))
    set_setting("tts_enabled", "true" if tts_enabled else "false")
    set_setting("bgm_enabled", "true" if bgm_enabled else "false")

    print("\n" + "=" * 65)
    print("\033[1;32m✨ SETUP COMPLETE! Your Karaoke Lounge is ready to rock. ✨\033[0m")
    print(f"🍹 Bar Name:        {bar_name}")
    print(f"🔑 Host 4-Digit PIN: {host_pin}")
    print(f"📁 Video Library:   {lib_path}")
    print(f"📂 Database & Data: {data_path}")
    print(f"🌐 Remote Mode:     {remote_mode.upper()}")
    if remote_mode == "bore":
        print(f"👉 Permanent URL:   http://bore.pub:{remote_port}")
    print("=" * 65)
    print("\nTo start your Karaoke Lounge now, run:")
    print("   \033[1;33m./start.sh\033[0m\n")

if __name__ == "__main__":
    run_setup()
