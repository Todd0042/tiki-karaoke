"""
Hardware & Initial Setup Wizard Routes for Tiki Karaoke.
Handles display detection, browser selection, audio output/input sinks,
storage path configuration, directory picking, and setup wizard persistence.
"""

import os
import sys
import time
import shutil
import subprocess
import tempfile
import urllib.parse
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from config import (
    is_configured, load_config, save_config,
    get_library_dir, get_data_dir, get_recordings_dir, get_default_recordings_dir,
    get_tracks_dir, get_db_path,
    PORT, STATIC_DIR, HOST_TOKEN,
    find_system_browser, get_available_displays, get_available_browsers,
    get_available_audio_sinks, get_available_audio_sources, get_path_info,
    get_system_drives
)
from database import init_db, migrate_storage_paths, get_setting, set_setting
import audio_router
from services.websocket_manager import manager
from services.auth import is_host_request, is_local_request
from services.templates import templates
from services.lifecycle import get_tunnel_reload_trigger

router = APIRouter()


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        return RedirectResponse(url="/", status_code=303)
    cfg = load_config()
    return templates.TemplateResponse(request=request, name="setup.html", context={
        "config": cfg,
        "default_library_dir": str(get_library_dir()),
        "default_data_dir": str(get_data_dir()),
        "default_recordings_dir": str(get_default_recordings_dir()),
        "current_recordings_dir": str(get_recordings_dir()),
        "displays": get_available_displays(),
        "audio_sinks": get_available_audio_sinks(),
        "audio_sources": get_available_audio_sources(),
        "browsers": get_available_browsers()
    })


@router.get("/api/setup/displays")
async def api_get_setup_displays(request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_available_displays()


@router.get("/api/setup/browsers")
async def api_get_setup_browsers(request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_available_browsers()


@router.get("/api/setup/audio-devices")
async def api_get_setup_audio_devices(request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_available_audio_sinks()


@router.get("/api/setup/audio-inputs")
async def api_get_setup_audio_inputs(request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_available_audio_sources()


@router.get("/stage/identify", response_class=HTMLResponse)
async def stage_identify_page(request: Request, id: int = 0, name: str = "Display", role: str = "stage"):
    is_host = (role == "host")
    emoji = "👑" if is_host else "🌴"
    title_prefix = "HOST DJ CONTROL DISPLAY" if is_host else "STAGE DISPLAY"
    border_color = "#ffb703" if is_host else "#00f0ff"
    card_border = "#ff007f" if is_host else "#39ff14"
    h1_color = "#ffb703" if is_host else "#39ff14"
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{title_prefix} #{id} Identified</title>
        <style>
            body {{
                margin: 0;
                padding: 0;
                background: #080e18;
                color: #00f0ff;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 100vh;
                overflow: hidden;
                box-sizing: border-box;
                border: 12px solid {border_color};
                box-shadow: inset 0 0 100px rgba(0, 240, 255, 0.5);
            }}
            .card {{
                background: rgba(13, 21, 38, 0.95);
                border: 2px solid {card_border};
                border-radius: 24px;
                padding: 40px 60px;
                text-align: center;
                box-shadow: 0 0 60px rgba(57, 255, 20, 0.4);
                max-width: 85%;
            }}
            h1 {{ font-size: 3.6rem; margin: 0 0 12px 0; color: {h1_color}; text-shadow: 0 0 25px rgba(255, 183, 3, 0.6); }}
            h2 {{ font-size: 2.2rem; margin: 0 0 16px 0; color: #00f0ff; }}
            p {{ font-size: 1.3rem; margin: 0; color: #39ff14; font-weight: bold; }}
            .sub {{ font-size: 1.05rem; color: #94a3b8; margin-top: 25px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div style="font-size: 4.5rem; margin-bottom: 8px;">{emoji}</div>
            <h1>{title_prefix} #{id}</h1>
            <h2>{name}</h2>
            <p>✅ Target Screen Identified Successfully!</p>
            <div class="sub">Closing automatically...</div>
        </div>
        <script>
            setTimeout(function() {{
                try {{ window.close(); }} catch (e) {{}}
            }}, 3500);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/api/setup/test-display")
async def api_test_display(payload: Dict[str, Any], request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    disp_id = int(payload.get("display_id", 0))
    disp_name = payload.get("name", f"Monitor {disp_id}")
    role = payload.get("role", "stage")
    badge_prefix = "👑 HOST DJ CONTROL DISPLAY" if role == "host" else "📺 MAIN STAGE DISPLAY"
    badge = payload.get("badge", f"{badge_prefix} #{disp_id}: {disp_name}")
    
    # 1. Broadcast test badge to any connected stage displays via WebSocket
    await manager.broadcast({
        "type": "test_display",
        "badge": badge
    })

    # 2. Resolve target physical screen coordinates
    displays = get_available_displays()
    target_disp = next((d for d in displays if d.get("id") == disp_id), None)
    disp_x = payload.get("x", target_disp.get("x", 0) if target_disp else 0)
    disp_y = payload.get("y", target_disp.get("y", 0) if target_disp else 0)
    disp_w = payload.get("width", target_disp.get("width", 1920) if target_disp else 1920)
    disp_h = payload.get("height", target_disp.get("height", 1080) if target_disp else 1080)

    port = int(payload.get("port", PORT))
    encoded_name = urllib.parse.quote(disp_name)
    ident_url = f"http://127.0.0.1:{port}/stage/identify?id={disp_id}&name={encoded_name}&role={role}"

    # 3. Locate available browser (Edge, Chrome, Chromium, Brave, or Firefox)
    browser_bin = None
    browser_type = None
    for b_name in ["microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "firefox"]:
        found = find_system_browser(b_name)
        if found:
            browser_bin = found
            browser_type = "firefox" if "firefox" in b_name else "chromium"
            break

    # In automated test suite or headless run, skip launching real desktop process
    if os.environ.get("TIKI_DATA_DIR") or os.environ.get("TIKI_TESTING"):
        return {
            "status": "ok",
            "url": ident_url,
            "x": disp_x,
            "y": disp_y,
            "width": disp_w,
            "height": disp_h
        }

    if browser_bin:
        try:
            temp_root = Path(tempfile.gettempdir())
            profile_dir = temp_root / f"tiki_ident_profile_{disp_id}_{os.getpid()}"
            profile_dir.mkdir(parents=True, exist_ok=True)

            if browser_type == "firefox":
                cmd = [
                    browser_bin,
                    "--no-remote",
                    "--profile", str(profile_dir),
                    "--new-instance",
                    "--kiosk",
                    ident_url
                ]
            else:
                cmd = [
                    browser_bin,
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--new-window",
                    f"--window-position={disp_x},{disp_y}",
                    f"--window-size={disp_w},{disp_h}",
                    "--app=" + ident_url
                ]

            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception as e:
            print(f"[Test Display] Identification popup error: {e}")

    return {
        "status": "ok",
        "url": ident_url,
        "x": disp_x,
        "y": disp_y,
        "width": disp_w,
        "height": disp_h
    }


@router.post("/api/setup/test-audio")
async def api_test_audio(payload: Dict[str, Any], request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    device = payload.get("device", "default").strip()

    # In automated test suite or headless run, return ok without spawning lingering sound processes
    if os.environ.get("TIKI_TESTING") or os.environ.get("TIKI_DATA_DIR"):
        return {"status": "ok", "played": True, "device": device}

    chime_path = STATIC_DIR / "sounds" / "chime.wav"
    if not chime_path.exists():
        chime_path.parent.mkdir(parents=True, exist_ok=True)
        import wave, math, struct
        sample_rate = 44100
        duration = 1.6
        samples = []
        chords = [(0.0, 523.25), (0.2, 659.25), (0.4, 783.99)]
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            val = 0.0
            for start_t, freq in chords:
                if t >= start_t:
                    dt = t - start_t
                    env = math.exp(-3.5 * dt)
                    val += 0.35 * math.sin(2.0 * math.pi * freq * dt) * env
            val = max(-1.0, min(1.0, val))
            samples.append(int(val * 32767.0))
        with wave.open(str(chime_path), 'w') as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            frames = bytearray()
            for s in samples:
                frames.extend(struct.pack('<hh', s, s))
            wav_file.writeframes(frames)

    # 1. Windows: Native winsound asynchronous audio playback
    if sys.platform == "win32" and chime_path.exists():
        try:
            import winsound
            winsound.PlaySound(str(chime_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            return {"status": "ok", "played": True, "device": device}
        except Exception as e:
            print(f"[Test Audio] Windows winsound error: {e}")

    # 2. Linux: PulseAudio / PipeWire playback
    paplay_bin = shutil.which("paplay")
    pw_play_bin = shutil.which("pw-play")

    if paplay_bin and chime_path.exists():
        try:
            cmd = [paplay_bin]
            if device and device != "default":
                cmd.extend(["-d", device])
            cmd.append(str(chime_path))
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"status": "ok", "played": True, "device": device}
        except Exception as e:
            print(f"[Test Audio] paplay error: {e}")

    if pw_play_bin and chime_path.exists():
        try:
            cmd = [pw_play_bin]
            if device and device != "default":
                cmd.extend([f"--target={device}"])
            cmd.append(str(chime_path))
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"status": "ok", "played": True, "device": device}
        except Exception as e:
            print(f"[Test Audio] pw-play error: {e}")

    return {"status": "ok", "played": False, "device": device}


@router.post("/api/setup/inspect-path")
async def api_inspect_storage_path(payload: Dict[str, Any], request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    path_str = payload.get("path", "").strip()
    if not path_str:
        return {"valid": False, "error": "Path cannot be empty"}
    return get_path_info(path_str)


@router.post("/api/setup/pick-directory")
async def api_pick_directory(payload: Dict[str, Any], request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    
    current_path = payload.get("initial_path", "").strip()
    if not current_path or not Path(current_path).exists():
        current_path = str(Path.home())
    else:
        current_path = str(Path(current_path).expanduser().resolve())

    # In automated test suite or headless run, skip launching real desktop GUI dialogs
    if os.environ.get("TIKI_TESTING") or os.environ.get("TIKI_DATA_DIR"):
        return {"status": "ok", "path": current_path, "native": True}

    # Attempt native desktop folder picker if request is local
    if is_local_request(request):
        if sys.platform == "win32":
            try:
                safe_path = current_path.replace("'", "''")
                ps_script = (
                    "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                    "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                    "$f.Description = 'Select Directory for Tiki Karaoke'; "
                    f"$f.SelectedPath = '{safe_path}'; "
                    "$f.ShowNewFolderButton = $true; "
                    "$f.AutoUpgradeEnabled = $true; "
                    "$top = New-Object System.Windows.Forms.Form; "
                    "$top.TopMost = $true; "
                    "$top.WindowState = [System.Windows.Forms.FormWindowState]::Minimized; "
                    "$res = $f.ShowDialog($top); "
                    "if ($res -eq [System.Windows.Forms.DialogResult]::OK) { "
                    "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                    "  Write-Output $f.SelectedPath "
                    "}"
                )
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    creationflags=creationflags
                )
                chosen = proc.stdout.strip()
                if proc.returncode == 0 and chosen and Path(chosen).is_dir():
                    return {"status": "ok", "path": chosen, "native": True}
                elif proc.returncode == 0 and not chosen:
                    return {"status": "cancelled", "native": True}
            except Exception as e:
                print(f"[Pick Directory] Windows PowerShell picker error: {e}")

        # Linux: Native zenity or kdialog if desktop display server is present
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if has_display:
            zenity_bin = shutil.which("zenity")
            kdialog_bin = shutil.which("kdialog")

            if zenity_bin:
                try:
                    cmd = [
                        zenity_bin,
                        "--file-selection",
                        "--directory",
                        "--title=Select Directory for Tiki Karaoke",
                        f"--filename={current_path}/"
                    ]
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    if proc.returncode == 0:
                        chosen = proc.stdout.strip()
                        if chosen and Path(chosen).is_dir():
                            return {"status": "ok", "path": chosen, "native": True}
                    elif proc.returncode == 1:
                        return {"status": "cancelled", "native": True}
                except Exception as e:
                    print(f"[Pick Directory] zenity error: {e}")

            if kdialog_bin:
                try:
                    cmd = [
                        kdialog_bin,
                        "--getexistingdirectory",
                        current_path,
                        "--title",
                        "Select Directory for Tiki Karaoke"
                    ]
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    if proc.returncode == 0:
                        chosen = proc.stdout.strip()
                        if chosen and Path(chosen).is_dir():
                            return {"status": "ok", "path": chosen, "native": True}
                    elif proc.returncode == 1:
                        return {"status": "cancelled", "native": True}
                except Exception as e:
                    print(f"[Pick Directory] kdialog error: {e}")

    # Fallback to web browser directory modal
    return {"status": "fallback", "native": False, "initial_path": current_path}


@router.get("/api/setup/browse-directories")
async def api_browse_directories(request: Request, path: str = ""):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    path = path.strip()

    # Windows: Handle "This PC" / drives root listing
    if sys.platform == "win32":
        if not path or path.lower() in ("this pc", "computer", "drives", "root"):
            drives = get_system_drives()
            return {
                "status": "ok",
                "current": "This PC",
                "parent": None,
                "is_drives_view": True,
                "directories": drives
            }

    if not path or path == "~":
        target = Path.home()
    else:
        target = Path(path).expanduser().resolve()

    if not target.exists() or not target.is_dir():
        target = Path.home()

    # Determine parent path
    parent_path = None
    if sys.platform == "win32":
        clean_target = str(target).rstrip("\\/").upper()
        drive_roots = [d.rstrip("\\/").upper() for d in get_system_drives()]
        if clean_target in drive_roots or target.parent == target:
            parent_path = "This PC"
        else:
            parent_path = str(target.parent)
    else:
        parent_path = str(target.parent) if target.parent != target else None

    subdirs = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                try:
                    entry.stat()
                    subdirs.append(entry.name)
                except (PermissionError, OSError):
                    continue
    except Exception as e:
        print(f"[Browse Directories] Error reading {target}: {e}")

    return {
        "status": "ok",
        "current": str(target),
        "parent": parent_path,
        "directories": subdirs
    }


@router.post("/api/setup")
async def api_save_setup(payload: Dict[str, Any], request: Request, response: Response):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    
    try:
        old_tracks = get_tracks_dir()
        old_db = get_db_path()

        # Normalize storage paths if supplied
        new_lib = payload.get("library_dir", "").strip()
        if new_lib:
            new_lib_path = Path(new_lib).expanduser().resolve()
            payload["library_dir"] = str(new_lib_path)
            new_tracks = new_lib_path / "tracks"

            # Auto-migrate existing tracks if path changed and old library has files
            if old_tracks.exists() and old_tracks.resolve() != new_tracks.resolve():
                existing_tracks = list(old_tracks.glob("*/*.mp4"))
                if existing_tracks and payload.get("migrate_files", True):
                    new_tracks.mkdir(parents=True, exist_ok=True)
                    for artist_dir in old_tracks.iterdir():
                        if artist_dir.is_dir() and artist_dir.name != "tracks":
                            target_artist = new_tracks / artist_dir.name
                            target_artist.mkdir(parents=True, exist_ok=True)
                            for f in artist_dir.iterdir():
                                dest_f = target_artist / f.name
                                if not dest_f.exists():
                                    shutil.move(str(f), str(dest_f))
                    migrate_storage_paths(str(old_tracks), str(new_tracks))

        new_data = payload.get("data_dir", "").strip()
        if new_data:
            new_data_path = Path(new_data).expanduser().resolve()
            payload["data_dir"] = str(new_data_path)
            new_db = new_data_path / "karaoke.db"
            if old_db.exists() and old_db.resolve() != new_db.resolve() and not new_db.exists():
                new_data_path.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(old_db), str(new_db))

        new_recs = payload.get("recordings_dir", "").strip()
        if new_recs:
            new_recs_path = Path(new_recs).expanduser().resolve()
            payload["recordings_dir"] = str(new_recs_path)
            new_recs_path.mkdir(parents=True, exist_ok=True)
            set_setting("recordings_dir", str(new_recs_path))
        elif "recordings_dir" in payload:
            payload["recordings_dir"] = ""
            set_setting("recordings_dir", "")

        cfg = save_config(payload)
        init_db()

        if "remote_mode" in payload:
            set_setting("remote_mode", str(payload["remote_mode"]))
        if "hotspot_enabled" in payload:
            set_setting("hotspot_enabled", "true" if payload["hotspot_enabled"] else "false")
        if "hotspot_ssid" in payload:
            set_setting("hotspot_ssid", str(payload["hotspot_ssid"]).strip())
        if "hotspot_password" in payload:
            set_setting("hotspot_password", str(payload["hotspot_password"]).strip())
        if "bar_name" in payload:
            set_setting("bar_name", str(payload["bar_name"]))
        if "intermission_seconds" in payload:
            set_setting("intermission_seconds", str(payload["intermission_seconds"]))
        if "tts_enabled" in payload:
            set_setting("tts_enabled", "true" if payload["tts_enabled"] else "false")
        if "bgm_enabled" in payload:
            set_setting("bgm_enabled", "true" if payload["bgm_enabled"] else "false")
        if "stage_display_enabled" in payload:
            set_setting("stage_display_enabled", "true" if payload["stage_display_enabled"] else "false")
        if "stage_display" in payload:
            set_setting("stage_display", str(payload["stage_display"]))
        if "stage_display_name" in payload:
            set_setting("stage_display_name", str(payload["stage_display_name"]))
        if "stage_display_x" in payload:
            set_setting("stage_display_x", str(payload["stage_display_x"]))
        if "stage_display_y" in payload:
            set_setting("stage_display_y", str(payload["stage_display_y"]))
        if "stage_display_width" in payload:
            set_setting("stage_display_width", str(payload["stage_display_width"]))
        if "stage_display_height" in payload:
            set_setting("stage_display_height", str(payload["stage_display_height"]))
        if "audio_output_device" in payload:
            set_setting("audio_output_device", str(payload["audio_output_device"]))
        if "preferred_browser" in payload:
            set_setting("preferred_browser", str(payload["preferred_browser"]).strip().lower())
        if "kiosk_mode" in payload:
            set_setting("kiosk_mode", "true" if payload["kiosk_mode"] else "false")
        if "host_display_enabled" in payload:
            set_setting("host_display_enabled", "true" if payload["host_display_enabled"] else "false")
        if "host_display" in payload:
            set_setting("host_display", str(payload["host_display"]))
        if "host_display_name" in payload:
            set_setting("host_display_name", str(payload["host_display_name"]))
        if "host_display_x" in payload:
            set_setting("host_display_x", str(payload["host_display_x"]))
        if "host_display_y" in payload:
            set_setting("host_display_y", str(payload["host_display_y"]))
        if "host_display_width" in payload:
            set_setting("host_display_width", str(payload["host_display_width"]))
        if "host_display_height" in payload:
            set_setting("host_display_height", str(payload["host_display_height"]))
        if "host_kiosk_mode" in payload:
            set_setting("host_kiosk_mode", "true" if payload["host_kiosk_mode"] else "false")
        if "video_quality_profile" in payload:
            set_setting("video_quality_profile", str(payload["video_quality_profile"]))
        if "mic_monitoring_mode" in payload:
            from audio_router import MONITORING_MODES
            mode = str(payload["mic_monitoring_mode"]).strip()
            if mode in MONITORING_MODES:
                set_setting("mic_monitoring_mode", mode)
                audio_router.set_monitoring_mode(mode)

        # Automatically log host in with cookie
        response.set_cookie(
            "tiki_host",
            HOST_TOKEN,
            max_age=86400 * 30,
            httponly=False,
            samesite="lax"
        )

        tunnel_reload_cb = get_tunnel_reload_trigger()
        if tunnel_reload_cb:
            try:
                await asyncio.to_thread(tunnel_reload_cb)
            except Exception as te:
                print(f"[Setup] Tunnel reload notice: {te}")

        # Broadcast updated network info to any connected displays/clients
        try:
            import network_helper
            from config import PORT
            effective_port = int(os.getenv("TIKI_PORT", str(PORT)))
            ip = network_helper.get_best_local_ip()
            is_hotspot = network_helper.is_hotspot_ip(ip)
            is_net = network_helper.is_internet_available()
            ssid = network_helper.get_hotspot_ssid()
            password = network_helper.get_hotspot_password()
            show_wifi_card = network_helper.is_hotspot_enabled()
            from app import get_base_url
            base_url = get_base_url()
            await manager.broadcast({
                "type": "network_updated",
                "local_ip": ip,
                "port": effective_port,
                "base_url": base_url,
                "join_url": f"{base_url}/join",
                "is_internet_connected": is_net,
                "is_hotspot": is_hotspot,
                "hotspot_enabled": get_setting("hotspot_enabled", "false") == "true",
                "hotspot_ssid": ssid,
                "hotspot_password": password,
                "show_wifi_card": show_wifi_card
            })
        except Exception:
            pass

        return {"status": "ok", "redirect": f"/host?token={HOST_TOKEN}", "config": cfg}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "detail": f"Setup save failed: {str(e)}"})
