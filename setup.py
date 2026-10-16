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
    CONFIG_FILE, load_config, save_config, is_configured, DEFAULT_CONFIG
)
from database import init_db, set_setting

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
        "google_client_secret": current.get("google_client_secret", "")
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
    print(f"🌐 Remote Mode:     {remote_mode.upper()}")
    if remote_mode == "bore":
        print(f"👉 Permanent URL:   http://bore.pub:{remote_port}")
    print("=" * 65)
    print("\nTo start your Karaoke Lounge now, run:")
    print("   \033[1;33m./start.sh\033[0m\n")

if __name__ == "__main__":
    run_setup()
