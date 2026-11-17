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
    get_path_info, ensure_storage_dirs, get_available_displays, get_available_audio_sinks,
    get_available_audio_sources, get_available_browsers
)
from database import init_db, set_setting, migrate_storage_paths

TIKI_SETUP_BANNER = r"""
  _______ _ _    _    _  __                     _        
 |__   __(_) |  (_)  | |/ /                    | |       
    | |   _| | ___   | ' / __ _ _ __ __ _  ___ | | _____ 
    | |  | | |/ / |  |  < / _` | '__/ _` |/ _ \| |/ / _ \
    | |  | |   <| |  | . \ (_| | | | (_| | (_) |   <  __/
    |_|  |_|_|\_\_|  |_|\_\__,_|_|  \__,_|\___/|_|\_\___|
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

def _prompt_int(text: str, default: int) -> int:
    try:
        return int(prompt(text, str(default)))
    except ValueError:
        return default


def _prompt_float(text: str, default: float) -> float:
    try:
        return float(prompt(text, str(default)))
    except ValueError:
        return default


def _configure_identity(current: dict) -> dict:
    print("\n👑 1. LOUNGE IDENTITY & HOST SECURITY")
    return {
        "bar_name": prompt("Bar / Venue Name", current.get("bar_name") or "The Tiki Hut Karaoke Lounge"),
        "host_pin": prompt("Host DJ Master Remote PIN (4 digits)", current.get("host_pin") or "1234"),
        "port": _prompt_int("Local Web Server Port", int(current.get("port") or 8000)),
    }


def _configure_remote_access(current: dict) -> dict:
    print("\n🌐 2. REMOTE CONNECTIVITY & SSL / HTTPS")
    print("  [1] Cloudflare Quick Tunnel (Recommended) - Instant trusted 🔒 HTTPS, zero setup, no account needed")
    print("  [2] Cloudflare Named Tunnel (Custom Domain) - Permanent branded domain (e.g. sing.mybar.com) with 🔒 HTTPS")
    print("  [3] Tailscale Funnel - Free permanent 🔒 HTTPS (requires tailscale CLI, works behind CGNAT)")
    print("  [4] DuckDNS + Caddy - Free subdomain (e.g. mybar.duckdns.org) with auto Let's Encrypt SSL")
    print("  [5] Local Wi-Fi Network Only - Offline / internal LAN only (http://192.168.x.x:8000)")
    print("  [6] Bore Static Port (Legacy) - Fixed public port on bore.pub (HTTP only, unencrypted)")

    choice = prompt("Select Remote Mode [1-6]", "1")
    res = {
        "remote_mode": "cloudflare_quick",
        "public_host": "",
        "remote_port": int(current.get("remote_port") or 38480),
        "cloudflare_token": current.get("cloudflare_token", ""),
        "custom_domain": current.get("custom_domain", ""),
        "tailscale_domain": current.get("tailscale_domain", ""),
        "duckdns_domain": current.get("duckdns_domain", ""),
        "duckdns_token": current.get("duckdns_token", ""),
    }

    if choice == "2":
        res["remote_mode"] = "cloudflare_named"
        res["cloudflare_token"] = prompt("Paste your Cloudflare Tunnel Token", res["cloudflare_token"])
        res["custom_domain"] = prompt("Enter your public domain (e.g. sing.mybar.com)", res["custom_domain"])
        res["public_host"] = res["custom_domain"]
    elif choice == "3":
        res["remote_mode"] = "tailscale"
        res["tailscale_domain"] = prompt("Enter your Tailscale domain (leave blank to auto-detect)", res["tailscale_domain"])
        res["public_host"] = res["tailscale_domain"]
    elif choice == "4":
        res["remote_mode"] = "duckdns"
        res["duckdns_domain"] = prompt("DuckDNS Subdomain (e.g. my-lounge without .duckdns.org)", res["duckdns_domain"])
        res["duckdns_token"] = prompt("DuckDNS Account Token", res["duckdns_token"])
        res["public_host"] = f"{res['duckdns_domain']}.duckdns.org" if res["duckdns_domain"] else ""
    elif choice == "5":
        res["remote_mode"] = "local"
    elif choice == "6":
        res["remote_mode"] = "bore"
        res["remote_port"] = _prompt_int("Permanent Port on bore.pub", res["remote_port"])
        res["public_host"] = f"bore.pub:{res['remote_port']}"
    return res


def _configure_audio_stage(current: dict) -> dict:
    print("\n🔊 3. AUDIO & STAGE EXPERIENCE")
    return {
        "target_lufs": _prompt_float("Audio Loudness Target LUFS (EBU R128 Broadcast Standard)", float(current.get("target_lufs") or -23.0)),
        "intermission_seconds": _prompt_int("Intermission Countdown Seconds between songs", int(current.get("intermission_seconds") or 20)),
        "bgm_enabled": prompt("Enable Tropical Background Intermission Music (BGM)? [Y/n]", "y").lower().startswith("y"),
        "tts_enabled": prompt("Enable Virtual DJ TTS Voice Announcer? [y/N]", "n").lower().startswith("y"),
    }


def _verify_and_migrate_storage(current: dict) -> dict:
    print("\n💾 4. STORAGE & MEDIA DIRECTORIES")
    lib_path = Path(prompt("Video Media Library directory", current.get("library_dir") or str(get_library_dir()))).expanduser().resolve()
    info_lib = get_path_info(str(lib_path))
    print(f"  ✅ Drive verified: {info_lib['free_gb']} GB free on {info_lib['path']}" if info_lib.get("valid") else f"  ⚠️ Warning on library path: {info_lib.get('error', 'Unverified')}")

    data_path = Path(prompt("Database directory (karaoke.db & runtime)", current.get("data_dir") or str(get_data_dir()))).expanduser().resolve()
    info_data = get_path_info(str(data_path))
    print(f"  ✅ Drive verified: {info_data['free_gb']} GB free on {info_data['path']}" if info_data.get("valid") else f"  ⚠️ Warning on data path: {info_data.get('error', 'Unverified')}")

    old_tracks = get_tracks_dir()
    new_tracks = lib_path / "tracks"
    if old_tracks.exists() and old_tracks.resolve() != new_tracks.resolve():
        existing_tracks = list(old_tracks.glob("*/*.mp4"))
        if existing_tracks and prompt(f"Migrate {len(existing_tracks)} video files to new location? [Y/n]", "y").lower().startswith("y"):
            new_tracks.mkdir(parents=True, exist_ok=True)
            for artist_dir in old_tracks.iterdir():
                if artist_dir.is_dir() and artist_dir.name != "tracks":
                    target_artist = new_tracks / artist_dir.name
                    target_artist.mkdir(parents=True, exist_ok=True)
                    for f in artist_dir.iterdir():
                        dest_f = target_artist / f.name
                        if not dest_f.exists():
                            shutil.move(str(f), str(dest_f))
            updated_count = migrate_storage_paths(str(old_tracks), str(new_tracks))
            print(f"  ✅ {len(existing_tracks)} files moved and {updated_count} database paths updated!")

    old_db = get_db_path()
    new_db = data_path / "karaoke.db"
    if old_db.exists() and old_db.resolve() != new_db.resolve() and not new_db.exists():
        data_path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(old_db), str(new_db))
        print("  ✅ Copied existing karaoke.db to new data directory!")

    return {"library_dir": str(lib_path), "data_dir": str(data_path)}


def _configure_displays(current: dict) -> dict:
    print("\n🖥️ 5. MAIN STAGE DISPLAY CHOOSER")
    displays = get_available_displays()
    for d in displays:
        mark = " ★ Primary Desktop" if d.get("is_primary") else " (External / TV)"
        print(f"  [{d['id']}] {d['name']} - {d['width']}x{d['height']} at +{d['x']}+{d['y']}{mark}")

    chosen_idx = _prompt_int(f"Select monitor for Main Stage Display [0-{len(displays)-1}]", int(current.get("stage_display", 0)))
    chosen_disp = next((d for d in displays if d["id"] == chosen_idx), displays[0])
    kiosk_mode = prompt("Launch in Dedicated Fullscreen Kiosk Mode? [Y/n]", "y").lower().startswith("y")

    host_disp_enabled = False
    chosen_host = {"id": 1, "name": "Secondary Display", "x": 0, "y": 0, "width": 1920, "height": 1080}
    host_kiosk = False
    if len(displays) > 1 and prompt("Auto-launch Host DJ Window on secondary monitor? [Y/n]", "y" if current.get("host_display_enabled") else "n").lower().startswith("y"):
        host_disp_enabled = True
        default_host_idx = current.get("host_display", 1 if chosen_idx != 1 else 0)
        host_idx = _prompt_int(f"Select monitor for Host DJ Control Window [0-{len(displays)-1}]", int(default_host_idx))
        chosen_host = next((d for d in displays if d["id"] == host_idx), displays[0])
        host_kiosk = prompt("Launch Host DJ Window in Fullscreen Kiosk Mode? [y/N]", "y" if current.get("host_kiosk_mode") else "n").lower().startswith("y")

    return {
        "stage_display": chosen_disp["id"], "stage_display_name": chosen_disp["name"],
        "stage_display_x": chosen_disp["x"], "stage_display_y": chosen_disp["y"],
        "stage_display_width": chosen_disp["width"], "stage_display_height": chosen_disp["height"],
        "kiosk_mode": kiosk_mode,
        "host_display_enabled": host_disp_enabled,
        "host_display": chosen_host["id"], "host_display_name": chosen_host["name"],
        "host_display_x": chosen_host["x"], "host_display_y": chosen_host["y"],
        "host_display_width": chosen_host["width"], "host_display_height": chosen_host["height"],
        "host_kiosk_mode": host_kiosk,
    }


def _pick_indexed_option(items: list, prompt_label: str) -> dict:
    idx = _prompt_int(f"{prompt_label} [0-{max(0, len(items) - 1)}]", 0)
    return items[idx] if 0 <= idx < len(items) else items[0]


def _configure_devices_and_browser(current: dict) -> dict:
    print("\n🔊 6. STAGE AUDIO OUTPUT & INPUT DEVICES")
    sinks = get_available_audio_sinks()
    for idx, s in enumerate(sinks):
        print(f"  [{idx}] {s['description']}")
    chosen_sink = _pick_indexed_option(sinks, "Select Audio Device for Karaoke Stage")

    sources = get_available_audio_sources()
    for idx, s in enumerate(sources):
        print(f"  [{idx}] {s['description']}")
    chosen_source = _pick_indexed_option(sources, "Select Microphone / Input Device")

    print("\n🌐 7. PREFERRED WEB BROWSER")
    browsers = get_available_browsers()
    for idx, b in enumerate(browsers):
        avail = " (Available)" if b.get("available") else " (Not Installed)"
        print(f"  [{idx}] {b['name']} ({b['id']}) - {b['badge']}{avail}")
    chosen_browser = prompt("Select Preferred Browser [auto, firefox, chrome, edge, brave, chromium, default]", str(current.get("preferred_browser", "auto"))).strip().lower() or "auto"

    return {
        "audio_output_device": chosen_sink["id"],
        "audio_output_name": chosen_sink["description"],
        "audio_input_device": chosen_source["id"],
        "audio_input_name": chosen_source["description"],
        "preferred_browser": chosen_browser,
    }


def _persist_and_summarize(config_data: dict) -> None:
    print("\n💾 Writing configuration to config.json...")
    save_config(config_data)
    init_db()
    for key in ("bar_name", "intermission_seconds", "stage_display", "host_display", "host_display_name", "audio_output_device"):
        set_setting(key, str(config_data[key]))
    for bkey in ("tts_enabled", "bgm_enabled", "host_display_enabled", "host_kiosk_mode", "kiosk_mode"):
        set_setting(bkey, "true" if config_data[bkey] else "false")

    print("\n" + "=" * 65)
    print("\033[1;32m✨ SETUP COMPLETE! Your Karaoke Lounge is ready to rock. ✨\033[0m")
    print(f"🍹 Bar Name:        {config_data['bar_name']}")
    print(f"🔑 Host 4-Digit PIN: {config_data['host_pin']}")
    print(f"📁 Video Library:   {config_data['library_dir']}")
    print(f"📂 Database & Data: {config_data['data_dir']}")
    print(f"🌐 Remote Mode:     {config_data['remote_mode'].upper()}")
    print("=" * 65)
    print("\nTo start your Karaoke Lounge now, run:\n   \033[1;33m./start.sh\033[0m\n")


def run_setup():
    print(TIKI_SETUP_BANNER)
    print("Welcome! Let's set up your Tiki Karaoke Lounge.\n")
    check_dependencies()
    current = load_config()

    config_data = {
        "configured": True,
        "bgm_volume": 0.25,
        "google_client_id": current.get("google_client_id", ""),
        "google_client_secret": current.get("google_client_secret", ""),
    }
    config_data.update(_configure_identity(current))
    config_data.update(_configure_remote_access(current))
    config_data.update(_configure_audio_stage(current))
    config_data.update(_verify_and_migrate_storage(current))
    config_data.update(_configure_displays(current))
    config_data.update(_configure_devices_and_browser(current))
    _persist_and_summarize(config_data)


if __name__ == "__main__":
    run_setup()
