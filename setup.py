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
    get_available_audio_sources
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

def run_setup():
    print(TIKI_SETUP_BANNER)
    print("Welcome! Let's set up your Tiki Karaoke Lounge.")
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

    print("\n🌐 2. REMOTE CONNECTIVITY & SSL / HTTPS")
    print("  [1] Cloudflare Quick Tunnel (Recommended) - Instant trusted 🔒 HTTPS, zero setup, no account needed")
    print("  [2] Cloudflare Named Tunnel (Custom Domain) - Permanent branded domain (e.g. sing.mybar.com) with 🔒 HTTPS")
    print("  [3] Tailscale Funnel - Free permanent 🔒 HTTPS (requires tailscale CLI, works behind CGNAT)")
    print("  [4] DuckDNS + Caddy - Free subdomain (e.g. mybar.duckdns.org) with auto Let's Encrypt SSL")
    print("  [5] Local Wi-Fi Network Only - Offline / internal LAN only (http://192.168.x.x:8000)")
    print("  [6] Bore Static Port (Legacy) - Fixed public port on bore.pub (HTTP only, unencrypted)")

    choice = prompt("Select Remote Mode [1-6]", "1")
    remote_mode = "cloudflare_quick"
    public_host = ""
    remote_port = 38480
    cloudflare_token = current.get("cloudflare_token", "")
    custom_domain = current.get("custom_domain", "")
    tailscale_domain = current.get("tailscale_domain", "")
    duckdns_domain = current.get("duckdns_domain", "")
    duckdns_token = current.get("duckdns_token", "")

    if choice == "2":
        remote_mode = "cloudflare_named"
        print("\n  📋 Cloudflare Named Tunnel Setup:")
        print("     1. Go to https://one.dash.cloudflare.com -> Networks -> Tunnels")
        print("     2. Create a tunnel (name it anything you like, e.g. your bar or lounge name)")
        print("     3. Select 'cloudflared' and copy the Tunnel Token")
        print("     4. Add a Public Hostname (e.g. sing.mybar.com) pointing to HTTP 127.0.0.1:8000")
        cloudflare_token = prompt("Paste your Cloudflare Tunnel Token", cloudflare_token)
        custom_domain = prompt("Enter your public domain (e.g. sing.mybar.com)", custom_domain)
        public_host = custom_domain
    elif choice == "3":
        remote_mode = "tailscale"
        print("\n  📋 Tailscale Funnel Setup:")
        print("     1. Ensure Tailscale is installed on this machine: curl -fsSL https://tailscale.com/install.sh | sh")
        print("     2. Enable Funnel in your Tailscale admin console.")
        tailscale_domain = prompt("Enter your Tailscale domain (leave blank to auto-detect via tailscale status)", tailscale_domain)
        public_host = tailscale_domain
    elif choice == "4":
        remote_mode = "duckdns"
        print("\n  📋 DuckDNS + Caddy Setup:")
        print("     1. Register a free subdomain at https://www.duckdns.org")
        print("     2. Ensure router forwards ports 80 & 443 to this machine.")
        duckdns_domain = prompt("DuckDNS Subdomain (e.g. my-lounge without .duckdns.org)", duckdns_domain)
        duckdns_token = prompt("DuckDNS Account Token", duckdns_token)
        public_host = f"{duckdns_domain}.duckdns.org" if duckdns_domain else ""
    elif choice == "5":
        remote_mode = "local"
    elif choice == "6":
        remote_mode = "bore"
        remote_port_str = prompt("Permanent Port on bore.pub", str(current.get("remote_port") or 38480))
        try:
            remote_port = int(remote_port_str)
        except ValueError:
            remote_port = 38480
        public_host = f"bore.pub:{remote_port}"
    else:
        remote_mode = "cloudflare_quick"

    print("\n🔊 3. AUDIO & STAGE EXPERIENCE")
    lufs_str = prompt("Audio Loudness Target LUFS (EBU R128 Broadcast Standard)", str(current.get("target_lufs") or -23.0))
    try:
        target_lufs = float(lufs_str)
    except ValueError:
        target_lufs = -23.0

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
    print("Ideal for secondary high-speed NVMe SSDs or dedicated storage drives (e.g. /media/storage/karaoke or ~/karaoke).")
    
    current_lib = current.get("library_dir") or str(get_library_dir())
    current_data = current.get("data_dir") or str(get_data_dir())

    lib_input = prompt("Paste or select the directory you wish to use for songs (Video Media Library)", current_lib)
    lib_path = Path(lib_input).expanduser().resolve()
    info_lib = get_path_info(str(lib_path))
    if info_lib.get("valid"):
        print(f"  ✅ Drive verified: \033[1;32m{info_lib['free_gb']} GB\033[0m free on {info_lib['path']}")
    else:
        print(f"  ⚠️ Warning on library path: {info_lib.get('error', 'Unverified')}")

    data_input = prompt("Paste or select the directory you wish to use for database (karaoke.db & runtime)", current_data)
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

    # 5. Display Chooser
    print("\n🖥️ 5. MAIN STAGE DISPLAY CHOOSER")
    print("Detected Connected Display Monitors:")
    displays = get_available_displays()
    for d in displays:
        primary_mark = " ★ Primary Desktop" if d.get("is_primary") else " (External / TV)"
        print(f"  [{d['id']}] {d['name']} - {d['width']}x{d['height']} at offset +{d['x']}+{d['y']}{primary_mark}")
    
    current_disp_id = current.get("stage_display", 0)
    disp_input = prompt(f"Select monitor for Main Stage Display [0-{len(displays)-1}]", str(current_disp_id))
    try:
        chosen_disp_idx = int(disp_input)
    except ValueError:
        chosen_disp_idx = 0
    
    chosen_disp = next((d for d in displays if d["id"] == chosen_disp_idx), displays[0])
    print(f"  ✅ Selected Stage Monitor: {chosen_disp['name']} ({chosen_disp['width']}x{chosen_disp['height']})")

    kiosk_choice = prompt("Launch in Dedicated Fullscreen Kiosk Mode (borderless window on TV)? [Y/n]", "y")
    kiosk_mode = kiosk_choice.lower().startswith("y")

    # Host DJ Display for multi-screen setups
    host_disp_enabled = False
    chosen_host_disp = {"id": 1, "name": "Secondary Display", "x": 0, "y": 0, "width": 1920, "height": 1080}
    host_kiosk_mode = False
    if len(displays) > 1:
        auto_host = prompt("Auto-launch Host DJ Control Window on a secondary monitor on startup? [Y/n]", "y" if current.get("host_display_enabled") else "n")
        if auto_host.lower().startswith("y"):
            host_disp_enabled = True
            default_host_idx = current.get("host_display", 1 if chosen_disp_idx != 1 else 0)
            host_input = prompt(f"Select monitor for Host DJ Control Window [0-{len(displays)-1}]", str(default_host_idx))
            try:
                host_idx = int(host_input)
            except ValueError:
                host_idx = 1
            chosen_host_disp = next((d for d in displays if d["id"] == host_idx), displays[0])
            print(f"  ✅ Selected Host DJ Monitor: {chosen_host_disp['name']} ({chosen_host_disp['width']}x{chosen_host_disp['height']})")
            host_kiosk_choice = prompt("Launch Host DJ Window in Fullscreen Kiosk Mode? [y/N]", "y" if current.get("host_kiosk_mode") else "n")
            host_kiosk_mode = host_kiosk_choice.lower().startswith("y")

    # 6. Audio Output Device
    print("\n🔊 6. STAGE AUDIO OUTPUT DEVICE")
    print("Detected System Audio Devices:")
    audio_sinks = get_available_audio_sinks()
    for idx, s in enumerate(audio_sinks):
        print(f"  [{idx}] {s['description']}")
    
    sink_input = prompt(f"Select Audio Device for Karaoke Stage [0-{len(audio_sinks)-1}]", "0")
    try:
        sink_idx = int(sink_input)
        chosen_sink = audio_sinks[sink_idx] if 0 <= sink_idx < len(audio_sinks) else audio_sinks[0]
    except (ValueError, IndexError):
        chosen_sink = audio_sinks[0]
    print(f"  ✅ Selected Audio Output: {chosen_sink['description']}")

    # 7. Audio Input Device (Microphone / Line-In)
    print("\n🎤 7. STAGE MICROPHONE & AUDIO INPUT DEVICE")
    print("Detected System Microphones & Audio Inputs:")
    audio_sources = get_available_audio_sources()
    for idx, s in enumerate(audio_sources):
        print(f"  [{idx}] {s['description']}")
    
    source_input = prompt(f"Select Microphone / Input Device for Karaoke Stage [0-{len(audio_sources)-1}]", "0")
    try:
        source_idx = int(source_input)
        chosen_source = audio_sources[source_idx] if 0 <= source_idx < len(audio_sources) else audio_sources[0]
    except (ValueError, IndexError):
        chosen_source = audio_sources[0]
    print(f"  ✅ Selected Audio Input: {chosen_source['description']}")

    # Build updated config
    config_data = {
        "configured": True,
        "bar_name": bar_name,
        "host_pin": host_pin,
        "port": port,
        "remote_mode": remote_mode,
        "remote_port": remote_port,
        "public_host": public_host,
        "cloudflare_token": cloudflare_token,
        "custom_domain": custom_domain,
        "tailscale_domain": tailscale_domain,
        "duckdns_domain": duckdns_domain,
        "duckdns_token": duckdns_token,
        "target_lufs": target_lufs,
        "intermission_seconds": intermission_seconds,
        "tts_enabled": tts_enabled,
        "bgm_enabled": bgm_enabled,
        "bgm_volume": 0.25,
        "google_client_id": current.get("google_client_id", ""),
        "google_client_secret": current.get("google_client_secret", ""),
        "library_dir": str(lib_path),
        "data_dir": str(data_path),
        "stage_display": chosen_disp["id"],
        "stage_display_name": chosen_disp["name"],
        "stage_display_x": chosen_disp["x"],
        "stage_display_y": chosen_disp["y"],
        "stage_display_width": chosen_disp["width"],
        "stage_display_height": chosen_disp["height"],
        "kiosk_mode": kiosk_mode,
        "host_display_enabled": host_disp_enabled,
        "host_display": chosen_host_disp["id"],
        "host_display_name": chosen_host_disp["name"],
        "host_display_x": chosen_host_disp["x"],
        "host_display_y": chosen_host_disp["y"],
        "host_display_width": chosen_host_disp["width"],
        "host_display_height": chosen_host_disp["height"],
        "host_kiosk_mode": host_kiosk_mode,
        "audio_output_device": chosen_sink["id"],
        "audio_output_name": chosen_sink["description"],
        "audio_input_device": chosen_source["id"],
        "audio_input_name": chosen_source["description"]
    }

    print("\n💾 Writing configuration to config.json...")
    save_config(config_data)

    print("📁 Initializing database schema and default settings...")
    init_db()
    set_setting("bar_name", bar_name)
    set_setting("intermission_seconds", str(intermission_seconds))
    set_setting("tts_enabled", "true" if tts_enabled else "false")
    set_setting("bgm_enabled", "true" if bgm_enabled else "false")
    set_setting("stage_display", str(chosen_disp["id"]))
    set_setting("host_display_enabled", "true" if host_disp_enabled else "false")
    set_setting("host_display", str(chosen_host_disp["id"]))
    set_setting("host_display_name", chosen_host_disp["name"])
    set_setting("host_kiosk_mode", "true" if host_kiosk_mode else "false")
    set_setting("audio_output_device", chosen_sink["id"])
    set_setting("kiosk_mode", "true" if kiosk_mode else "false")

    print("\n" + "=" * 65)
    print("\033[1;32m✨ SETUP COMPLETE! Your Karaoke Lounge is ready to rock. ✨\033[0m")
    print(f"🍹 Bar Name:        {bar_name}")
    print(f"🔑 Host 4-Digit PIN: {host_pin}")
    print(f"📁 Video Library:   {lib_path}")
    print(f"📂 Database & Data: {data_path}")
    print(f"🌐 Remote Mode:     {remote_mode.upper()}")
    if remote_mode == "cloudflare_quick":
        print("👉 SSL / HTTPS:     Cloudflare Quick Tunnel (Auto HTTPS on launch)")
    elif remote_mode == "cloudflare_named":
        print(f"👉 Branded Domain:  https://{custom_domain}")
    elif remote_mode == "tailscale":
        print(f"👉 Tailscale Funnel: {tailscale_domain or 'Auto-detected'}")
    elif remote_mode == "duckdns":
        print(f"👉 DuckDNS Domain:  https://{duckdns_domain}.duckdns.org")
    elif remote_mode == "bore":
        print(f"👉 Permanent URL:   http://bore.pub:{remote_port}")
    else:
        print("👉 Access:          Local Wi-Fi Only")
    print("=" * 65)
    print("\nTo start your Karaoke Lounge now, run:")
    print("   \033[1;33m./start.sh\033[0m\n")

if __name__ == "__main__":
    run_setup()
