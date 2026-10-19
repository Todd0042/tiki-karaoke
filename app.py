import os
import io
import sys
import json
import time
import socket
import asyncio
import secrets
import subprocess
import signal
import threading
import shutil
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import qrcode

from config import (
    STATIC_DIR, TEMPLATES_DIR, TRACKS_DIR, HOST_TOKEN, HOST_PIN,
    PORT, DEFAULT_INTERMISSION_SECONDS, PUBLIC_HOST, REMOTE_MODE, BAR_NAME,
    load_config, save_config, is_configured,
    get_library_dir, get_tracks_dir, get_playlists_dir, get_data_dir,
    get_db_path, get_path_info, ensure_storage_dirs, get_storage_status,
    get_available_displays, get_available_audio_sinks
)
from database import (
    init_db, get_or_create_singer, get_singer_by_token,
    save_track, get_track_by_id, get_track_by_video_id,
    search_local_tracks, enqueue_song, get_queue,
    get_current_playing, set_queue_status, remove_from_queue,
    reorder_queue, get_singer_history, get_party_wrapped_stats,
    get_setting, set_setting, get_all_settings,
    get_singers_with_queue, remove_all_songs_by_singer,
    get_taken_avatars_for_name, purge_all_singers,
    migrate_storage_paths, get_browsable_tracks,
    get_unique_downloaded_artists, get_cleanup_candidates,
    prune_local_track, check_song_queue_duplicate, add_duet_partner,
    remove_duet_partner, clear_queue_on_startup, get_singer_songbook,
    get_pending_queue_count_for_singer,
    add_filler_track, get_filler_tracks, get_filler_track_by_id,
    get_filler_track_by_video_id, update_filler_track_status,
    update_filler_track_metadata, delete_filler_track,
    record_filler_play, get_next_vibe_filler_track, checkout_singer
)
from search import tiered_search, query_youtube_music_videos
from downloader import (
    downloader_worker, enqueue_download, register_download_callback,
    filler_downloader_worker, enqueue_filler_download, register_filler_callback
)
from metadata import fetch_track_metadata
from playlist_sync import (
    is_authenticated as is_yt_authenticated,
    get_auth_url, exchange_code, sync_played_song_to_youtube
)

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def get_public_host() -> str:
    return os.getenv("TIKI_PUBLIC_HOST", "").strip() or PUBLIC_HOST

def get_public_ip() -> str:
    host = get_public_host()
    if host:
        return host
    return get_local_ip()

LOCAL_IP = get_local_ip()
PUBLIC_IP = get_public_ip()

def get_base_url() -> str:
    from config import PORT, REMOTE_MODE
    cfg = load_config()
    mode = cfg.get("remote_mode", REMOTE_MODE)
    host = get_public_host()
    if mode != "local" and host:
        if host.startswith("http://") or host.startswith("https://"):
            return host
        if os.getenv("TIKI_USE_HTTPS") == "1" or "trycloudflare.com" in host or ".ts.net" in host:
            return f"https://{host}"
        return f"http://{host}"
    return f"http://{get_local_ip()}:{PORT}"

# WebSocket Connection Hub
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.stage_displays: Dict[str, Dict[str, Any]] = {}
        self.ws_to_display_id: Dict[WebSocket, str] = {}
        self.host_connections: set = set()
        self.emoji_times: Dict[WebSocket, List[float]] = {}

    async def connect(self, websocket: WebSocket, is_host: bool = False):
        await websocket.accept()
        self.active_connections.append(websocket)
        if is_host:
            self.host_connections.add(websocket)
        self.emoji_times[websocket] = []

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.host_connections.discard(websocket)
        self.emoji_times.pop(websocket, None)
        disp_id = self.ws_to_display_id.pop(websocket, None)
        if disp_id and disp_id in self.stage_displays:
            del self.stage_displays[disp_id]

    def register_stage_display(self, websocket: WebSocket, display_id: str, name: str, is_muted: bool, ip: str = ""):
        self.stage_displays[display_id] = {
            "display_id": display_id,
            "name": name,
            "is_muted": is_muted,
            "ip": ip,
            "last_seen": time.time()
        }
        self.ws_to_display_id[websocket] = display_id

    def is_display_authorized(self, websocket: WebSocket, display_id: str) -> bool:
        if websocket in self.host_connections:
            return True
        return self.ws_to_display_id.get(websocket) == display_id

    def check_emoji_rate_limit(self, websocket: WebSocket) -> bool:
        now = time.time()
        times = self.emoji_times.get(websocket, [])
        times = [t for t in times if now - t < 1.0]
        if len(times) >= 5:
            self.emoji_times[websocket] = times
            return False
        times.append(now)
        self.emoji_times[websocket] = times
        return True

    def set_display_muted(self, display_id: str, is_muted: bool):
        if display_id in self.stage_displays:
            self.stage_displays[display_id]["is_muted"] = is_muted

    def get_stage_displays(self) -> List[Dict[str, Any]]:
        return list(self.stage_displays.values())

    async def broadcast(self, message: Dict[str, Any]):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()
current_intermission_end: Optional[float] = None

# BGM Filler Video State
filler_active: bool = False
filler_current_track: Optional[Dict[str, Any]] = None
filler_buffer_end: Optional[float] = None
filler_transition_state: str = "idle"  # 'idle', 'buffer_15s', 'playing', 'fading_out', 'finishing'
filler_buffer_task: Optional[asyncio.Task] = None
last_karaoke_track: Optional[Dict[str, Any]] = None

# Background download completion notifications
async def on_download_finished(track_id: int, local_path: Optional[str]):
    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue,
        "track_id": track_id,
        "ready": bool(local_path)
    })

register_download_callback(on_download_finished)

async def on_filler_download_finished(filler_id: int, local_path: Optional[str]):
    track = get_filler_track_by_id(filler_id)
    await manager.broadcast({
        "type": "filler_track_updated",
        "track": track,
        "filler_id": filler_id,
        "ready": bool(local_path)
    })

register_filler_callback(on_filler_download_finished)

async def trigger_filler_sequence(last_artist: str = "", last_genre: str = "", last_year: int = 0):
    global filler_active, filler_transition_state, filler_buffer_end, filler_buffer_task, filler_current_track
    if get_setting("bgm_video_enabled", "true") != "true":
        return

    next_track = get_next_vibe_filler_track(last_artist, last_genre, last_year)
    if not next_track:
        return

    # If queue is empty, start downloaded music video immediately without text countdown
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    if not pending:
        filler_active = True
        filler_transition_state = "playing"
        filler_current_track = next_track
        filler_buffer_end = None
        record_filler_play(next_track["id"])
        await manager.broadcast({
            "type": "play_filler_track",
            "track": next_track
        })
        return

    buffer_seconds = int(get_setting("bgm_buffer_seconds", "15"))
    filler_active = True
    filler_transition_state = "buffer_15s"
    filler_buffer_end = time.time() + buffer_seconds
    filler_current_track = None

    await manager.broadcast({
        "type": "start_filler_buffer",
        "duration": buffer_seconds,
        "message": "OPEN MIC! Scan to take the stage!"
    })

    async def _wait_and_play():
        global filler_active, filler_transition_state, filler_buffer_end, filler_current_track
        try:
            await asyncio.sleep(buffer_seconds)
            if filler_active and filler_transition_state == "buffer_15s":
                queue = get_queue()
                if not queue:
                    pick = get_next_vibe_filler_track(last_artist, last_genre, last_year)
                    if pick:
                        filler_current_track = pick
                        filler_transition_state = "playing"
                        filler_buffer_end = None
                        record_filler_play(pick["id"])
                        await manager.broadcast({
                            "type": "play_filler_track",
                            "track": pick
                        })
                else:
                    filler_active = False
                    filler_transition_state = "idle"
                    filler_buffer_end = None
        except asyncio.CancelledError:
            pass

    if filler_buffer_task and not filler_buffer_task.done():
        filler_buffer_task.cancel()
    filler_buffer_task = asyncio.create_task(_wait_and_play())

async def on_filler_track_ended():
    global filler_active, filler_transition_state, filler_current_track, current_intermission_end
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]

    if pending:
        filler_active = False
        filler_transition_state = "idle"
        filler_current_track = None
        
        dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
        current_intermission_end = time.time() + dur
        await manager.broadcast({"type": "stop_filler"})
        await manager.broadcast({
            "type": "start_intermission",
            "next_track": pending[0],
            "duration": dur
        })
    else:
        last_artist = filler_current_track.get("artist", "") if filler_current_track else ""
        last_genre = filler_current_track.get("genre", "") if filler_current_track else ""
        next_track = get_next_vibe_filler_track(last_artist, last_genre)
        if next_track:
            filler_current_track = next_track
            filler_transition_state = "playing"
            record_filler_play(next_track["id"])
            await manager.broadcast({
                "type": "play_filler_track",
                "track": next_track
            })
        else:
            filler_active = False
            filler_transition_state = "idle"
            filler_current_track = None
            await manager.broadcast({"type": "stop_filler"})

async def intermission_watchdog_loop():
    """Background watchdog that ensures tracks automatically start when intermission timer ends,
    even if client browsers throttle setInterval or drop connection."""
    global current_intermission_end
    while True:
        try:
            await asyncio.sleep(1.0)
            if current_intermission_end and time.time() >= current_intermission_end:
                current = get_current_playing()
                if not current:
                    queue = get_queue()
                    pending = [q for q in queue if q["status"] == "pending"]
                    if pending:
                        next_up = pending[0]
                        current_intermission_end = None
                        print(f"[Watchdog] Intermission completed. Auto-starting track {next_up['queue_id']} ({next_up['title']})")
                        set_queue_status(next_up["queue_id"], "playing")
                        playing_track = get_current_playing()
                        await manager.broadcast({
                            "type": "play_track",
                            "track": playing_track
                        })
                        await manager.broadcast({
                            "type": "queue_updated",
                            "queue": get_queue()
                        })
                    else:
                        current_intermission_end = None
                else:
                    current_intermission_end = None
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Watchdog] Error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    clear_queue_on_startup()
    worker_task = asyncio.create_task(downloader_worker())
    filler_worker_task = asyncio.create_task(filler_downloader_worker())
    watchdog_task = asyncio.create_task(intermission_watchdog_loop())
    print("=" * 60)
    print("🌴 TIKI KARAOKE LOUNGE 🌴")
    print(f"👉 TV Main Stage Display: {get_base_url()}/stage")
    print(f"👉 Singer Mobile App:    {get_base_url()}/join")
    print(f"👉 Host DJ Remote URL:   {get_base_url()}/host?token={HOST_TOKEN}")
    print(f"👉 Host 4-Digit PIN:     {HOST_PIN}")
    print("=" * 60)
    yield
    worker_task.cancel()
    filler_worker_task.cancel()
    watchdog_task.cancel()

app = FastAPI(title="Tiki Karaoke", lifespan=lifespan)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path in ("/join", "/stage", "/host", "/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Request Models
class SingerRegisterRequest(BaseModel):
    token: str
    name: str
    avatar: str = "🌺"

class QueueSongRequest(BaseModel):
    track_id: Optional[int] = None
    video_id: str
    title: str
    artist: str
    singer_token: str
    duet_partner: str = ""
    pitch_shift: int = 0
    force: bool = False

class JoinDuetRequest(BaseModel):
    singer_token: str
    partner_name: Optional[str] = None

class LeaveDuetRequest(BaseModel):
    singer_token: str
    singer_name: Optional[str] = None

class PruneTracksRequest(BaseModel):
    track_ids: List[int]

# Host Auth Helper
def is_host_request(request: Request) -> bool:
    if not HOST_TOKEN:
        return False
    cookie_token = request.cookies.get("tiki_host")
    if cookie_token == HOST_TOKEN:
        return True
    auth_header = request.headers.get("X-Host-Token")
    if auth_header == HOST_TOKEN:
        return True
    query_token = request.query_params.get("token")
    if query_token == HOST_TOKEN:
        return True
    return False

def is_local_request(request: Request) -> bool:
    """Returns True if the request originates from localhost or the host machine itself."""
    if not request.client:
        return True
    h = request.client.host
    return h in ("127.0.0.1", "::1", "localhost", "testclient")

class PinAuthRequest(BaseModel):
    pin: str

failed_pin_attempts: Dict[str, List[float]] = {}

@app.post("/api/auth/pin")
async def api_auth_pin(req: PinAuthRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = [t for t in failed_pin_attempts.get(client_ip, []) if now - t < 60.0]
    if len(attempts) >= 5:
        failed_pin_attempts[client_ip] = attempts
        raise HTTPException(
            status_code=429,
            detail="Too many failed PIN attempts. Please wait 1 minute before trying again."
        )

    cfg = load_config()
    expected_pin = str(cfg.get("host_pin", HOST_PIN)).strip()
    provided_pin = str(req.pin).strip()

    if provided_pin == expected_pin:
        failed_pin_attempts.pop(client_ip, None)
        response = JSONResponse({"status": "ok", "is_host": True})
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 30, httponly=False)
        return response
    else:
        attempts.append(now)
        failed_pin_attempts[client_ip] = attempts
        raise HTTPException(status_code=401, detail="Invalid Host PIN. Please check the TV display.")

# Page Routes
@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        return RedirectResponse(url="/", status_code=303)
    cfg = load_config()
    return templates.TemplateResponse(request=request, name="setup.html", context={
        "config": cfg,
        "default_library_dir": str(get_library_dir()),
        "default_data_dir": str(get_data_dir()),
        "displays": get_available_displays(),
        "audio_sinks": get_available_audio_sinks()
    })

@app.get("/api/setup/displays")
async def api_get_setup_displays(request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_available_displays()

@app.get("/api/setup/audio-devices")
async def api_get_setup_audio_devices(request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_available_audio_sinks()

@app.get("/stage/identify", response_class=HTMLResponse)
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
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/api/setup/test-display")
async def api_test_display(payload: Dict[str, Any], request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    disp_id = int(payload.get("display_id", 0))
    disp_name = payload.get("name", f"Monitor {disp_id}")
    role = payload.get("role", "stage")
    badge_prefix = "👑 HOST DJ CONTROL DISPLAY" if role == "host" else "📺 MAIN STAGE DISPLAY"
    badge = payload.get("badge", f"{badge_prefix} #{disp_id}: {disp_name}")
    
    # 1. Broadcast test badge to any connected stage displays
    await manager.broadcast({
        "type": "test_display",
        "badge": badge
    })

    # 2. Launch momentary popup on that exact physical monitor
    firefox_bin = shutil.which("firefox")
    if firefox_bin:
        try:
            profile_dir = Path(f"/tmp/tiki_identify_profile_{disp_id}")
            profile_dir.mkdir(parents=True, exist_ok=True)
            port = int(payload.get("port", PORT))
            encoded_name = urllib.parse.quote(disp_name)
            ident_url = f"http://localhost:{port}/stage/identify?id={disp_id}&name={encoded_name}&role={role}"
            cmd = [
                firefox_bin,
                "--no-remote",
                "--profile", str(profile_dir),
                "--new-instance",
                "--kiosk",
                f"--kiosk-monitor={disp_id}",
                ident_url
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
            
            def close_ident():
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    pass
                try:
                    shutil.rmtree(profile_dir, ignore_errors=True)
                except Exception:
                    pass

            threading.Timer(3.5, close_ident).start()
        except Exception as e:
            print(f"[Test Display] Could not launch identification window: {e}")

    return {"status": "ok"}

@app.post("/api/setup/test-audio")
async def api_test_audio(payload: Dict[str, Any], request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    device = payload.get("device", "default").strip()

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

@app.post("/api/setup/inspect-path")
async def api_inspect_storage_path(payload: Dict[str, Any], request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    path_str = payload.get("path", "").strip()
    if not path_str:
        return {"valid": False, "error": "Path cannot be empty"}
    return get_path_info(path_str)

@app.post("/api/setup/pick-directory")
async def api_pick_directory(payload: Dict[str, Any], request: Request):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    
    current_path = payload.get("initial_path", "").strip()
    if not current_path or not Path(current_path).exists():
        current_path = str(Path.home())
    else:
        current_path = str(Path(current_path).expanduser().resolve())

    # Attempt native desktop file dialog if running on local desktop with display
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if has_display and is_local_request(request):
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

@app.get("/api/setup/browse-directories")
async def api_browse_directories(request: Request, path: str = ""):
    if is_configured() and not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    if not path or path == "~":
        target = Path.home()
    else:
        target = Path(path).expanduser().resolve()

    if not target.exists() or not target.is_dir():
        target = Path.home()

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

@app.post("/api/setup")
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

        cfg = save_config(payload)
        init_db()

        if "bar_name" in payload:
            set_setting("bar_name", str(payload["bar_name"]))
        if "intermission_seconds" in payload:
            set_setting("intermission_seconds", str(payload["intermission_seconds"]))
        if "tts_enabled" in payload:
            set_setting("tts_enabled", "true" if payload["tts_enabled"] else "false")
        if "bgm_enabled" in payload:
            set_setting("bgm_enabled", "true" if payload["bgm_enabled"] else "false")
        if "stage_display" in payload:
            set_setting("stage_display", str(payload["stage_display"]))
        if "audio_output_device" in payload:
            set_setting("audio_output_device", str(payload["audio_output_device"]))
        if "kiosk_mode" in payload:
            set_setting("kiosk_mode", "true" if payload["kiosk_mode"] else "false")
        if "host_display_enabled" in payload:
            set_setting("host_display_enabled", "true" if payload["host_display_enabled"] else "false")
        if "host_display" in payload:
            set_setting("host_display", str(payload["host_display"]))
        if "host_display_name" in payload:
            set_setting("host_display_name", str(payload["host_display_name"]))
        if "host_kiosk_mode" in payload:
            set_setting("host_kiosk_mode", "true" if payload["host_kiosk_mode"] else "false")
        if "video_quality_profile" in payload:
            set_setting("video_quality_profile", str(payload["video_quality_profile"]))

        # Automatically log host in with cookie
        response.set_cookie(
            "tiki_host",
            HOST_TOKEN,
            max_age=86400 * 30,
            httponly=False,
            samesite="lax"
        )
        return {"status": "ok", "redirect": "/stage", "config": cfg}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "detail": f"Setup save failed: {str(e)}"})

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    if not is_configured():
        return RedirectResponse(url="/setup")
    pub_h = get_public_host()
    return templates.TemplateResponse(request=request, name="mobile.html", context={
        "bar_name": get_setting("bar_name", "The Tiki Hut Karaoke Lounge"),
        "local_ip": LOCAL_IP,
        "public_ip": PUBLIC_IP,
        "base_url": get_base_url(),
        "display_ip": pub_h if pub_h else f"{PUBLIC_IP}:{PORT}",
        "port": PORT
    })

@app.get("/join", response_class=HTMLResponse)
async def join_page(request: Request):
    if not is_configured():
        return RedirectResponse(url="/setup")
    pub_h = get_public_host()
    return templates.TemplateResponse(request=request, name="mobile.html", context={
        "bar_name": get_setting("bar_name", "The Tiki Hut Karaoke Lounge"),
        "local_ip": LOCAL_IP,
        "public_ip": PUBLIC_IP,
        "base_url": get_base_url(),
        "display_ip": pub_h if pub_h else f"{PUBLIC_IP}:{PORT}",
        "port": PORT
    })

@app.get("/stage", response_class=HTMLResponse)
async def stage_page(request: Request, token: str = ""):
    if not is_configured():
        return RedirectResponse(url="/setup")
    pub_h = get_public_host()
    cfg = load_config()
    pin = str(cfg.get("host_pin", HOST_PIN))
    response = templates.TemplateResponse(request=request, name="host.html", context={
        "bar_name": get_setting("bar_name", "The Tiki Hut Karaoke Lounge"),
        "local_ip": LOCAL_IP,
        "public_ip": PUBLIC_IP,
        "base_url": get_base_url(),
        "display_ip": pub_h if pub_h else f"{PUBLIC_IP}:{PORT}",
        "port": PORT,
        "host_pin": pin
    })
    if (token and token == HOST_TOKEN) or is_host_request(request) or is_local_request(request):
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 30, httponly=False)
    return response

@app.get("/host")
async def host_pair_route(request: Request, token: str = ""):
    if token and token == HOST_TOKEN:
        response = RedirectResponse(url="/")
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 7, httponly=False)
        return response
    return HTMLResponse("<h3>Invalid Host Token!</h3><p>Scan the Host QR code from the main TV screen.</p>", status_code=403)

@app.get("/host/print-tents")
async def print_table_tents(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    cfg = load_config()
    default_bar = cfg.get("bar_name") or BAR_NAME or "The Tiki Hut Karaoke Lounge"
    bar_name = get_setting("bar_name", default_bar)
    return templates.TemplateResponse(
        request=request,
        name="print_tents.html",
        context={
            "bar_name": bar_name,
            "base_url": get_base_url(),
            "join_url": f"{get_base_url()}/join"
        }
    )

# QR Code Generators (Point directly to Public IP)
@app.get("/qr/public")
async def public_qr():
    url = f"{get_base_url()}/join"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.get("/qr/host")
async def host_qr(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required to view host QR code.")
    url = f"{get_base_url()}/host?token={HOST_TOKEN}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# Local Video Stream
@app.get("/stream/{track_id}")
async def stream_local_video(track_id: int):
    track = get_track_by_id(track_id)
    if not track or not track["is_downloaded"] or not track["local_path"]:
        raise HTTPException(status_code=404, detail="Track not available locally")
    
    file_path = track["local_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File missing on disk")

    return FileResponse(file_path, media_type="video/mp4")

@app.get("/stream/filler/{filler_id}")
async def stream_filler_video(filler_id: int):
    track = get_filler_track_by_id(filler_id)
    if not track or not track["is_downloaded"] or not track["local_path"]:
        raise HTTPException(status_code=404, detail="Filler video not available locally")
    
    file_path = track["local_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Filler video file missing on disk")

    return FileResponse(file_path, media_type="video/mp4")

# Search API
search_requests_by_ip: Dict[str, List[float]] = {}

@app.get("/api/search")
async def api_search(request: Request, q: str = "", force_online: bool = False):
    if not is_host_request(request):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        attempts = [t for t in search_requests_by_ip.get(client_ip, []) if now - t < 60.0]
        if len(attempts) >= 20:
            search_requests_by_ip[client_ip] = attempts
            raise HTTPException(
                status_code=429,
                detail="Search rate limit reached. Please wait a few seconds before searching again."
            )
        attempts.append(now)
        search_requests_by_ip[client_ip] = attempts

    return tiered_search(q, force_online=force_online)

# Library Browsing API
@app.get("/api/library")
async def api_get_library(
    page: int = 1,
    limit: int = 25,
    sort: str = "popularity",
    artist: str = "",
    q: str = ""
):
    data = get_browsable_tracks(page=page, limit=limit, sort=sort, artist=artist, search=q)
    data["artists"] = get_unique_downloaded_artists()
    return data

# Storage Management API
@app.get("/api/storage/status")
async def api_get_storage_status():
    return get_storage_status()

@app.get("/api/storage/cleanup-candidates")
async def api_get_cleanup_candidates(sort_by: str = "oldest_played", limit: int = 50):
    return {
        "status": get_storage_status(),
        "candidates": get_cleanup_candidates(sort_by=sort_by, limit=limit)
    }

@app.post("/api/storage/prune")
async def api_prune_tracks(req: PruneTracksRequest, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host authentication required to prune files.")
    
    pruned_count = 0
    for tid in req.track_ids:
        if prune_local_track(tid):
            pruned_count += 1
    
    await manager.broadcast({
        "type": "library_updated",
        "queue": get_queue()
    })
    return {
        "status": "ok",
        "pruned_count": pruned_count,
        "storage": get_storage_status()
    }

# Singer API
@app.post("/api/singer/register")
async def api_register_singer(req: SingerRegisterRequest, request: Request):
    client_ip = request.client.host if request.client else ""
    is_host = is_host_request(request) or is_local_request(request)
    try:
        singer = get_or_create_singer(
            token=req.token,
            name=req.name,
            avatar=req.avatar,
            ip_address=client_ip,
            is_host=is_host
        )
        return singer
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@app.get("/api/singer/available-avatars")
async def api_available_avatars(request: Request, name: str = "", token: str = ""):
    client_ip = request.client.host if request.client else ""
    is_host = is_host_request(request) or is_local_request(request)
    taken = get_taken_avatars_for_name(name, exclude_token=token, client_ip=client_ip, is_host=is_host)
    return {
        "name": name,
        "taken_avatars": taken
    }

@app.get("/api/singer/{token}/history")
async def api_singer_history(token: str):
    singer = get_singer_by_token(token)
    if not singer:
        return []
    return get_singer_history(singer["id"])

@app.get("/api/singer/{token}/songbook")
async def api_singer_songbook(token: str):
    singer = get_singer_by_token(token)
    if not singer:
        return {
            "singer": None,
            "songs": [],
            "total_queued": 0,
            "unique_songs": 0
        }
    songs = get_singer_songbook(singer["id"])
    total_queued = sum(s.get("times_queued", 1) for s in songs)
    return {
        "singer": {
            "name": singer["name"],
            "avatar": singer["avatar"],
            "token": singer["token"]
        },
        "songs": songs,
        "total_queued": total_queued,
        "unique_songs": len(songs)
    }

@app.get("/api/singer/{token}/profile")
async def api_singer_profile(token: str):
    singer = get_singer_by_token(token)
    if not singer:
        raise HTTPException(status_code=404, detail="Singer profile not found.")
    return {
        "token": singer["token"],
        "name": singer["name"],
        "avatar": singer["avatar"]
    }

# Queue API
@app.get("/api/queue")
async def api_get_queue():
    return get_queue()

@app.get("/api/queue/check-duplicate")
async def api_check_duplicate(
    singer_token: str = "",
    track_id: Optional[int] = None,
    video_id: Optional[str] = None,
    title: Optional[str] = None,
    artist: Optional[str] = None
):
    singer = get_singer_by_token(singer_token) if singer_token else None
    singer_id = singer["id"] if singer else 0
    dup = check_song_queue_duplicate(
        singer_id=singer_id,
        track_id=track_id,
        video_id=video_id,
        title=title,
        artist=artist
    )
    return {"duplicate": dup}

@app.post("/api/queue/{queue_id}/join-duet")
async def api_join_duet(queue_id: int, req: JoinDuetRequest):
    singer = get_singer_by_token(req.singer_token)
    name = req.partner_name or (singer["name"] if singer else "Friend")
    res = add_duet_partner(queue_id, name)
    if not res:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    
    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })
    return {"status": "ok", "duet_partner": res["duet_partner"], "queue": queue}

@app.post("/api/queue/{queue_id}/leave-duet")
async def api_leave_duet(queue_id: int, req: LeaveDuetRequest):
    singer = get_singer_by_token(req.singer_token)
    name = req.singer_name or (singer["name"] if singer else "")
    res = remove_duet_partner(queue_id, singer_name=name, singer_token=req.singer_token)
    if not res:
        raise HTTPException(status_code=404, detail="Queue item not found.")

    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })
    return {"status": "ok", "result": res, "queue": queue}

last_queue_time_by_singer: Dict[str, float] = {}
last_queue_time_by_ip: Dict[str, float] = {}

@app.post("/api/queue")
async def api_enqueue_song(req: QueueSongRequest, request: Request):
    client_ip = request.client.host if request.client else ""
    is_host = is_host_request(request)

    singer = get_singer_by_token(req.singer_token)
    if not singer:
        token_suffix = (req.singer_token[-4:] if req.singer_token and len(req.singer_token) >= 4 else secrets.token_hex(2)).upper()
        try:
            singer = get_or_create_singer(req.singer_token, f"Guest {token_suffix}", ip_address=client_ip, is_host=is_host)
        except Exception:
            singer = get_or_create_singer(req.singer_token, f"Guest {secrets.token_hex(3).upper()}", ip_address=client_ip, is_host=is_host)

    # Abuse Prevention: 5s cooldown & 5-song quota (bypassed for Host DJ)
    singer_key = req.singer_token or client_ip
    if not is_host:
        now = time.time()
        last_t = last_queue_time_by_singer.get(singer_key, 0)
        if now - last_t < 5.0:
            raise HTTPException(
                status_code=429,
                detail="Please wait 5 seconds before adding another song."
            )

        pending_count = get_pending_queue_count_for_singer(singer["id"])
        if pending_count >= 5:
            raise HTTPException(
                status_code=400,
                detail="You already have 5 songs waiting in the queue! Please wait until you sing one before requesting another."
            )

    # Abuse Prevention: 10-minute video duration cap
    if req.track_id:
        existing_t = get_track_by_id(req.track_id)
        if existing_t and existing_t.get("duration") and existing_t["duration"] > 600:
            raise HTTPException(
                status_code=400,
                detail="This song is over 10 minutes long (maximum allowed is 10:00). Please pick a standard-length version!"
            )

    # Check for duplicates if not forced
    if not req.force:
        dup = check_song_queue_duplicate(
            singer_id=singer["id"],
            track_id=req.track_id,
            video_id=req.video_id,
            title=req.title,
            artist=req.artist
        )
        if dup:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "duplicate_warning",
                    "duplicate": dup,
                    "detail": "Song is already in queue or performed recently."
                }
            )

    # Ensure track is in DB
    track_id = req.track_id
    if not track_id:
        track_id = save_track(
            video_id=req.video_id,
            title=req.title,
            artist=req.artist,
            original_title=f"{req.artist} - {req.title}"
        )

    queue_id = enqueue_song(
        track_id=track_id,
        singer_id=singer["id"],
        duet_partner=req.duet_partner,
        pitch_shift=req.pitch_shift
    )

    if not is_host:
        now = time.time()
        last_queue_time_by_singer[singer_key] = now

    track = get_track_by_id(track_id)

    # Pre-cache / download track in background if not yet downloaded (unless in live stream-only mode)
    if track and not track["is_downloaded"]:
        cfg = load_config()
        if cfg.get("video_quality_profile") != "stream_only":
            enqueue_download(
                track_id=track_id,
                singer_name=singer["name"],
                title=req.title,
                artist=req.artist
            )

    current_playing = get_current_playing()
    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })

    # If nothing is currently playing and a stage display is connected, trigger intermission countdown!
    if not current_playing:
        global current_intermission_end, filler_active, filler_transition_state, filler_buffer_task
        if filler_active:
            if filler_transition_state == "buffer_15s":
                if filler_buffer_task and not filler_buffer_task.done():
                    filler_buffer_task.cancel()
                filler_active = False
                filler_transition_state = "idle"
                # Proceed directly to stage intermission
            elif filler_transition_state == "playing":
                policy = get_setting("bgm_transition_policy", "fade")
                next_song_item = queue[0] if queue else None
                if policy == "fade":
                    filler_transition_state = "fading_out"
                    await manager.broadcast({
                        "type": "filler_transition_fade",
                        "fade_duration": 3,
                        "next_singer": next_song_item["singer_name"] if next_song_item else "",
                        "next_title": next_song_item["title"] if next_song_item else "",
                        "next_artist": next_song_item["artist"] if next_song_item else ""
                    })

                    async def _delayed_fade_to_intermission(target_track):
                        global filler_active, filler_transition_state, current_intermission_end
                        try:
                            await asyncio.sleep(3)
                            filler_active = False
                            filler_transition_state = "idle"
                            dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
                            current_intermission_end = time.time() + dur
                            await manager.broadcast({"type": "stop_filler"})
                            await manager.broadcast({
                                "type": "start_intermission",
                                "next_track": target_track,
                                "duration": dur
                            })
                        except asyncio.CancelledError:
                            pass

                    asyncio.create_task(_delayed_fade_to_intermission(next_song_item))
                    return {"status": "ok", "queue_id": queue_id}
                else:  # 'finish' mode
                    filler_transition_state = "finishing"
                    await manager.broadcast({
                        "type": "filler_transition_finish",
                        "next_singer": next_song_item["singer_name"] if next_song_item else "",
                        "next_title": next_song_item["title"] if next_song_item else "",
                        "next_artist": next_song_item["artist"] if next_song_item else ""
                    })
                    return {"status": "ok", "queue_id": queue_id}

        dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
        current_intermission_end = time.time() + dur
        await manager.broadcast({
            "type": "start_intermission",
            "next_track": queue[0] if queue else None,
            "duration": dur
        })

    return {"status": "ok", "queue_id": queue_id}

@app.delete("/api/queue/{queue_id}")
async def api_remove_queue(queue_id: int, request: Request, token: str = ""):
    singer = get_singer_by_token(token)
    singer_id = singer["id"] if singer and not is_host_request(request) else None
    deleted = remove_from_queue(queue_id, singer_id=singer_id)
    if deleted:
        await manager.broadcast({
            "type": "queue_updated",
            "queue": get_queue()
        })
    return {"deleted": deleted}

@app.post("/api/queue/{queue_id}/play")
async def api_play_queue_item(queue_id: int, request: Request):
    if not is_host_request(request) and not is_local_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global current_intermission_end
    current_intermission_end = None
    set_queue_status(queue_id, "playing")
    current = get_current_playing()
    await manager.broadcast({
        "type": "play_track",
        "track": current
    })
    await manager.broadcast({
        "type": "queue_updated",
        "queue": get_queue()
    })
    return {"status": "ok", "playing": current}

@app.post("/api/queue/{queue_id}/complete")
async def api_complete_queue_item(queue_id: int, request: Request):
    if not is_host_request(request) and not is_local_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global current_intermission_end
    queue_list = get_queue()
    item = next((q for q in queue_list if q["queue_id"] == queue_id), None)

    set_queue_status(queue_id, "completed")

    # Sync to YouTube playlist in background if authenticated
    if item and is_yt_authenticated():
        asyncio.create_task(asyncio.to_thread(
            sync_played_song_to_youtube, item["singer_name"], item["video_id"]
        ))

    remaining = get_queue()
    next_up = remaining[0] if remaining else None

    await manager.broadcast({
        "type": "queue_updated",
        "queue": remaining
    })

    if next_up:
        dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
        current_intermission_end = time.time() + dur
        await manager.broadcast({
            "type": "start_intermission",
            "next_track": next_up,
            "duration": dur
        })
    else:
        current_intermission_end = None
        await manager.broadcast({
            "type": "start_intermission",
            "next_track": None,
            "duration": 0
        })
        last_artist = item.get("artist", "") if item else ""
        last_title = item.get("title", "") if item else ""
        last_genre = ""
        last_year = 0
        if last_artist and last_title:
            try:
                meta = fetch_track_metadata(last_artist, last_title)
                last_genre = meta.get("genre", "")
                last_year = meta.get("release_year", 0)
            except Exception:
                pass
        await trigger_filler_sequence(last_artist=last_artist, last_genre=last_genre, last_year=last_year)

    return {"status": "completed"}

@app.get("/api/state")
async def api_get_state():
    current = get_current_playing()
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    next_up = pending[0] if pending else None
    stage_displays = manager.get_stage_displays()
    
    intermission_remaining = 0
    if not current and next_up and current_intermission_end:
        intermission_remaining = max(0, int(current_intermission_end - time.time()))
    elif not current and next_up:
        intermission_remaining = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))

    # Prefer downloaded BGM videos when stage is idle and queue is empty
    global filler_active, filler_transition_state, filler_current_track, filler_buffer_end
    if not current and not next_up:
        if get_setting("bgm_video_enabled", "true") == "true" and not filler_active:
            pick = get_next_vibe_filler_track()
            if pick:
                filler_active = True
                filler_transition_state = "playing"
                filler_current_track = pick
                filler_buffer_end = None
                record_filler_play(pick["id"])

    return {
        "playing": current,
        "next": next_up,
        "queue": queue,
        "intermission_seconds": intermission_remaining,
        "has_stage_display": len(stage_displays) > 0,
        "waiting_for_display": False,
        "filler": {
            "active": filler_active,
            "state": filler_transition_state,
            "current_track": filler_current_track,
            "buffer_remaining": max(0, int(filler_buffer_end - time.time())) if filler_buffer_end else 0,
            "transition_policy": get_setting("bgm_transition_policy", "fade"),
            "bgm_video_enabled": get_setting("bgm_video_enabled", "true") == "true"
        },
        "av_sync_offset_ms": int(get_setting("av_sync_offset_ms", "0")),
        "system_health": {
            "ffmpeg_installed": bool(shutil.which("ffmpeg")),
            "port": int(os.getenv("TIKI_PORT", str(PORT)))
        },
        "network": {
            "base_url": get_base_url(),
            "remote_mode": REMOTE_MODE,
            "is_https": get_base_url().startswith("https://")
        }
    }

# Host Controls & Settings
@app.get("/api/host/health")
async def api_host_health(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    from config import PORT
    effective_port = int(os.getenv("TIKI_PORT", str(PORT)))
    return {
        "status": "ok",
        "ffmpeg_installed": bool(shutil.which("ffmpeg")),
        "ffprobe_installed": bool(shutil.which("ffprobe")),
        "port": effective_port,
        "python_version": sys.version.split()[0],
        "platform": sys.platform
    }

@app.get("/api/auth/role")
async def api_get_role(request: Request):
    return {"is_host": is_host_request(request)}

@app.get("/api/settings")
async def api_get_settings():
    return get_all_settings()

@app.post("/api/settings")
async def api_update_settings(settings: Dict[str, str], request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    for k, v in settings.items():
        set_setting(k, str(v))
    return {"status": "ok"}

@app.get("/api/bgm-tracks")
async def api_get_bgm_tracks():
    bgm_dir = STATIC_DIR / "bgm"
    if not bgm_dir.exists():
        return {"tracks": []}
    tracks = sorted([f"/static/bgm/{f.name}" for f in bgm_dir.glob("*.mp3")])
    return {"tracks": tracks}

@app.post("/api/host/tracks/{track_id}/download")
async def host_download_track(track_id: int, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    track = get_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    enqueue_download(
        track_id=track["id"],
        singer_name="Host DJ",
        title=track["title"],
        artist=track["artist"]
    )
    return {"status": "ok", "message": f"Downloading {track['artist']} - {track['title']} to library"}

@app.post("/api/host/skip")
async def host_skip_action(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global current_intermission_end
    current = get_current_playing()
    if current:
        set_queue_status(current["queue_id"], "skipped")
    else:
        queue = get_queue()
        pending = [q for q in queue if q["status"] == "pending"]
        if pending:
            set_queue_status(pending[0]["queue_id"], "skipped")

    await manager.broadcast({"type": "skip_track"})

    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    if pending:
        dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
        current_intermission_end = time.time() + dur
        await manager.broadcast({
            "type": "start_intermission",
            "next_track": pending[0],
            "duration": dur
        })
    else:
        current_intermission_end = None
        await manager.broadcast({"type": "queue_empty"})

    await manager.broadcast({"type": "queue_updated", "queue": queue})
    return {"status": "ok"}

@app.post("/api/host/stop")
async def host_stop_action(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global current_intermission_end
    current_intermission_end = None

    current = get_current_playing()
    if current:
        set_queue_status(current["queue_id"], "pending")

    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    next_track = pending[0] if pending else None

    await manager.broadcast({
        "type": "stop_track",
        "next_track": next_track
    })
    await manager.broadcast({"type": "queue_updated", "queue": queue})

    if get_setting("bgm_video_enabled", "true") == "true":
        await trigger_filler_sequence()

    return {"status": "ok"}

# ==============================================================================
# BGM FILLER & MUSIC VIDEOS API
# ==============================================================================

@app.get("/api/host/search-filler")
async def api_search_filler(request: Request, q: str = ""):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return query_youtube_music_videos(q)

@app.get("/api/host/filler-tracks")
async def api_get_filler_tracks(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_filler_tracks()

@app.post("/api/host/filler-tracks/stage")
async def api_stage_filler_tracks(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    data = await request.json()
    videos = data.get("videos", [])
    if not videos and "video_id" in data:
        videos = [data]

    staged_ids = []
    for v in videos:
        vid = v.get("video_id")
        if not vid:
            continue
        tid = add_filler_track(
            video_id=vid,
            title=v.get("title", ""),
            artist=v.get("artist", ""),
            original_title=v.get("original_title", v.get("title", "")),
            duration=v.get("duration", 0),
            genre=v.get("genre", ""),
            release_year=v.get("release_year", 0)
        )
        staged_ids.append(tid)

    await manager.broadcast({
        "type": "filler_tracks_updated",
        "tracks": get_filler_tracks()
    })
    return {"status": "ok", "staged_ids": staged_ids}

@app.post("/api/host/filler-tracks/download-batch")
async def api_download_filler_batch(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass

    track_ids = data.get("ids", [])
    all_tracks = get_filler_tracks()

    if not track_ids:
        to_download = [t for t in all_tracks if not t["is_downloaded"] and t["download_status"] != "downloading"]
    else:
        to_download = [t for t in all_tracks if t["id"] in track_ids and not t["is_downloaded"]]

    for t in to_download:
        update_filler_track_status(t["id"], "pending")
        enqueue_filler_download(t["id"])

    await manager.broadcast({
        "type": "filler_tracks_updated",
        "tracks": get_filler_tracks()
    })
    return {"status": "ok", "enqueued_count": len(to_download)}

@app.delete("/api/host/filler-tracks/{filler_id}")
async def api_delete_filler_track(filler_id: int, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    local_path = delete_filler_track(filler_id)
    if local_path and os.path.exists(local_path):
        try:
            os.unlink(local_path)
        except Exception as e:
            print(f"[Filler Delete] Could not remove {local_path}: {e}")

    await manager.broadcast({
        "type": "filler_tracks_updated",
        "tracks": get_filler_tracks()
    })
    return {"status": "ok", "deleted": True}

@app.post("/api/host/filler-tracks/{filler_id}/play")
async def api_force_play_filler(filler_id: int, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    track = get_filler_track_by_id(filler_id)
    if not track:
        raise HTTPException(status_code=404, detail="Filler track not found")

    global filler_active, filler_transition_state, filler_current_track, filler_buffer_end
    filler_active = True
    filler_transition_state = "playing"
    filler_current_track = track
    filler_buffer_end = None
    record_filler_play(filler_id)

    await manager.broadcast({
        "type": "play_filler_track",
        "track": track
    })
    return {"status": "ok", "playing": track}

@app.post("/api/host/filler-tracks/finished")
async def api_filler_finished(request: Request):
    await on_filler_track_ended()
@app.post("/api/host/filler/install-starter")
async def api_install_starter_crate(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    from scripts.seed_filler_crate import STARTER_FILLER_TRACKS
    existing = {t["video_id"]: t for t in get_filler_tracks()}
    added = []
    for track in STARTER_FILLER_TRACKS:
        vid = track["video_id"]
        if vid in existing:
            continue
        tid = add_filler_track(
            video_id=vid,
            title=track["title"],
            artist=track["artist"],
            original_title=f"{track['artist']} - {track['title']}",
            duration=track["duration"],
            genre=track["genre"],
            release_year=track["release_year"]
        )
        enqueue_filler_download(tid)
        added.append(track)
    await manager.broadcast({
        "type": "filler_tracks_updated",
        "tracks": get_filler_tracks()
    })
    return {"status": "ok", "added_count": len(added), "tracks": added}

@app.post("/api/host/filler/import-from-song/{track_id}")
async def api_import_filler_from_song(track_id: int, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    track = get_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    artist = track.get("artist", "")
    title = track.get("title", "")
    results = query_youtube_music_videos(f"{artist} {title} official music video", max_results=3)
    if not results:
        raise HTTPException(status_code=404, detail="No official music video found on YouTube")
    
    best = results[0]
    vid = best["video_id"]
    
    existing = get_filler_track_by_video_id(vid)
    if existing:
        return {"status": "already_exists", "message": f"{artist} - {title} is already in BGM crate", "track": existing}
    
    genre = "Pop"
    year = 0
    try:
        meta = fetch_track_metadata(artist, title)
        if meta:
            genre = meta.get("genre", "Pop")
            year = meta.get("release_year", 0)
    except Exception:
        pass
        
    tid = add_filler_track(
        video_id=vid,
        title=best.get("title") or title,
        artist=best.get("artist") or artist,
        original_title=best.get("original_title") or f"{artist} - {title}",
        duration=best.get("duration", 0),
        genre=genre,
        release_year=year
    )
    enqueue_filler_download(tid)
    await manager.broadcast({
        "type": "filler_tracks_updated",
        "tracks": get_filler_tracks()
    })
    return {"status": "ok", "message": f"Added official music video for {artist} - {title} to BGM!", "filler_id": tid}

@app.get("/api/host/filler-settings")
async def api_get_filler_settings(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return {
        "bgm_transition_policy": get_setting("bgm_transition_policy", "fade"),
        "bgm_video_enabled": get_setting("bgm_video_enabled", "true") == "true",
        "bgm_mode": get_setting("bgm_mode", "vibe"),
        "bgm_buffer_seconds": int(get_setting("bgm_buffer_seconds", "15"))
    }

@app.post("/api/host/filler-settings")
async def api_update_filler_settings(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    payload = await request.json()
    if "bgm_transition_policy" in payload:
        set_setting("bgm_transition_policy", str(payload["bgm_transition_policy"]))
    if "bgm_video_enabled" in payload:
        set_setting("bgm_video_enabled", "true" if payload["bgm_video_enabled"] else "false")
    if "bgm_mode" in payload:
        set_setting("bgm_mode", str(payload["bgm_mode"]))
    if "bgm_buffer_seconds" in payload:
        set_setting("bgm_buffer_seconds", str(payload["bgm_buffer_seconds"]))

    await manager.broadcast({
        "type": "filler_settings_updated",
        "settings": {
            "bgm_transition_policy": get_setting("bgm_transition_policy", "fade"),
            "bgm_video_enabled": get_setting("bgm_video_enabled", "true") == "true",
            "bgm_mode": get_setting("bgm_mode", "vibe"),
            "bgm_buffer_seconds": int(get_setting("bgm_buffer_seconds", "15"))
        }
    })
    return {"status": "ok"}

@app.post("/api/host/toggle-pause")
async def host_pause_action(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    current = get_current_playing()
    if not current:
        queue = get_queue()
        pending = [q for q in queue if q["status"] == "pending"]
        if pending:
            await api_play_queue_item(pending[0]["queue_id"], request)
            return {"status": "ok", "action": "started"}
    await manager.broadcast({"type": "pause_track"})
    return {"status": "ok"}

@app.post("/api/host/start-now")
async def host_start_now_action(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    if pending:
        next_track = pending[0]
        await api_play_queue_item(next_track["queue_id"], request)
    return {"status": "ok"}

@app.post("/api/host/add-time")
async def host_add_time_action(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    await manager.broadcast({
        "type": "start_intermission",
        "next_track": pending[0] if pending else None,
        "duration": 30
    })
    return {"status": "ok"}

@app.post("/api/host/stage/refresh")
async def host_refresh_stage_action(request: Request, hard: bool = False):
    if not is_host_request(request) and not is_local_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    if hard:
        await manager.broadcast({"type": "reload_display"})
    else:
        await manager.broadcast({"type": "resync_state"})
    await manager.broadcast({
        "type": "queue_updated",
        "queue": get_queue()
    })
    return {"status": "ok", "state": await api_get_state()}

@app.get("/api/host/singers")
async def host_get_singers(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_singers_with_queue()

@app.post("/api/host/singer/{singer_id}/remove-all")
async def host_remove_all_singer_songs(singer_id: int, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    checkout = False
    try:
        body = await request.json()
        checkout = bool(body.get("checkout", False))
    except Exception:
        checkout = request.query_params.get("checkout", "0") in ("1", "true", "True")

    deleted_count = remove_all_songs_by_singer(singer_id)
    if checkout:
        checkout_singer(singer_id)

    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })
    return {"status": "ok", "deleted_count": deleted_count, "checked_out": checkout}

@app.post("/api/singer/leave")
async def api_singer_leave(request: Request):
    token = request.query_params.get("token") or ""
    try:
        body = await request.json()
        token = body.get("token", token)
    except Exception:
        pass
    singer = get_singer_by_token(token)
    if not singer:
        return {"status": "ok", "message": "Singer not found"}
    remove_all_songs_by_singer(singer["id"])
    checkout_singer(singer["id"])
    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })
    return {"status": "ok", "checked_out": True}

@app.post("/api/host/singers/purge-all")
async def host_purge_all_singers(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    res = purge_all_singers(include_history=True)
    await manager.broadcast({
        "type": "queue_updated",
        "queue": []
    })
    return {"status": "ok", **res}

class DisplayAudioAction(BaseModel):
    action: str  # "mute", "unmute", "set_master"

@app.get("/api/host/displays")
async def host_get_displays(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return manager.get_stage_displays()

@app.post("/api/host/display/{display_id}/audio")
async def host_set_display_audio(display_id: str, req: DisplayAudioAction, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    if req.action == "mute":
        manager.set_display_muted(display_id, True)
        await manager.broadcast({
            "type": "set_display_audio",
            "display_id": display_id,
            "is_muted": True
        })
    elif req.action == "unmute":
        manager.set_display_muted(display_id, False)
        await manager.broadcast({
            "type": "set_display_audio",
            "display_id": display_id,
            "is_muted": False
        })
    elif req.action == "set_master":
        for d_id, disp in manager.stage_displays.items():
            should_mute = (d_id != display_id)
            disp["is_muted"] = should_mute
            await manager.broadcast({
                "type": "set_display_audio",
                "display_id": d_id,
                "is_muted": should_mute
            })

    await manager.broadcast({
        "type": "displays_updated",
        "displays": manager.get_stage_displays()
    })
    return {"status": "ok", "displays": manager.get_stage_displays()}

@app.get("/api/host/system/ytdlp-status")
async def host_get_ytdlp_status(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    current_version = "unknown"
    try:
        import yt_dlp
        current_version = getattr(yt_dlp.version, "__version__", "unknown")
    except Exception as e:
        print(f"[System] Failed to get yt_dlp version: {e}")

    latest_version = current_version
    try:
        def fetch_latest():
            req = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json", headers={"User-Agent": "TikiKaraoke/1.0"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("info", {}).get("version", current_version)
        latest_version = await asyncio.to_thread(fetch_latest)
    except Exception as e:
        print(f"[System] Failed to check latest yt-dlp version from PyPI: {e}")

    update_available = bool(latest_version and current_version and latest_version != current_version and latest_version != "unknown")

    return {
        "status": "ok",
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available
    }

@app.post("/api/host/system/update-ytdlp")
async def host_update_ytdlp(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    old_version = "unknown"
    try:
        import yt_dlp
        old_version = getattr(yt_dlp.version, "__version__", "unknown")
    except Exception:
        pass

    def run_upgrade():
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir", "yt-dlp"]
        print(f"[System] Running yt-dlp upgrade: {' '.join(cmd)}")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
        return proc

    try:
        proc = await asyncio.to_thread(run_upgrade)
    except Exception as e:
        print(f"[System] yt-dlp upgrade error: {e}")
        raise HTTPException(status_code=500, detail=f"Upgrade process failed: {e}")

    new_version = old_version
    try:
        import importlib
        import yt_dlp
        importlib.reload(yt_dlp)
        if hasattr(yt_dlp, "version"):
            importlib.reload(yt_dlp.version)
        new_version = getattr(yt_dlp.version, "__version__", old_version)
    except Exception as e:
        print(f"[System] Could not reload yt_dlp in-process: {e}")

    success = (proc.returncode == 0)
    return {
        "status": "success" if success else "error",
        "old_version": old_version,
        "new_version": new_version,
        "output": (proc.stdout or proc.stderr or "").strip(),
        "returncode": proc.returncode
    }

class AVSyncPayload(BaseModel):
    offset_ms: int

@app.get("/api/host/av-sync")
async def host_get_av_sync(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    raw = get_setting("av_sync_offset_ms", "0")
    try:
        offset_ms = int(raw)
    except Exception:
        offset_ms = 0
    return {"status": "ok", "offset_ms": offset_ms}

@app.post("/api/host/av-sync")
async def host_set_av_sync(payload: AVSyncPayload, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    offset_ms = max(-300, min(500, payload.offset_ms))
    set_setting("av_sync_offset_ms", str(offset_ms))
    await manager.broadcast({
        "type": "av_sync_updated",
        "offset_ms": offset_ms
    })
    return {"status": "ok", "offset_ms": offset_ms}

@app.post("/api/stage/notify-on-deck")
async def api_notify_on_deck(queue_id: int):
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    if pending and pending[0]["queue_id"] == queue_id:
        track = pending[0]
        await manager.broadcast({
            "type": "singer_on_deck",
            "queue_id": track["queue_id"],
            "singer_id": track["singer_id"],
            "singer_name": track.get("singer_name", ""),
            "duet_partner": track.get("duet_partner"),
            "title": track.get("title", ""),
            "artist": track.get("artist", ""),
            "seconds_remaining": 30
        })
    return {"status": "ok"}

@app.get("/api/surprise")
async def api_surprise():
    tracks = search_local_tracks("", limit=50)
    if not tracks:
        return None
    import random
    return random.choice(tracks)

@app.get("/api/wrapped")
async def api_wrapped():
    return get_party_wrapped_stats()

# Google OAuth 2.0
@app.get("/auth/login")
async def auth_login(request: Request):
    redirect_uri = f"http://{LOCAL_IP}:{PORT}/auth/callback"
    url = get_auth_url(redirect_uri)
    if not url:
        return HTMLResponse("<h3>Client secrets file not found</h3><p>Place client_secrets.json in the data/ folder to connect your Google account.</p>", status_code=400)
    return RedirectResponse(url)

@app.get("/auth/callback")
async def auth_callback(code: str = ""):
    redirect_uri = f"http://{LOCAL_IP}:{PORT}/auth/callback"
    success = exchange_code(code, redirect_uri)
    if success:
        return HTMLResponse("<h2>Google Account Connected Successfully! 🌺</h2><p>You can close this tab and return to the party.</p>")
    return HTMLResponse("<h2>Authentication failed.</h2>", status_code=400)

# WebSockets Hub
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global current_intermission_end
    is_host = False
    if HOST_TOKEN:
        cookie_token = websocket.cookies.get("tiki_host")
        if cookie_token == HOST_TOKEN:
            is_host = True
        elif websocket.headers.get("x-host-token") == HOST_TOKEN:
            is_host = True
        elif websocket.query_params.get("token") == HOST_TOKEN:
            is_host = True

    await manager.connect(websocket, is_host=is_host)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "time": time.time()})
                continue
            if msg_type == "stage_register":
                manager.register_stage_display(
                    websocket=websocket,
                    display_id=data.get("display_id", f"disp_{id(websocket)}"),
                    name=data.get("name", "Stage Display"),
                    is_muted=bool(data.get("is_muted", False)),
                    ip=websocket.client.host if websocket.client else ""
                )
                await manager.broadcast({
                    "type": "displays_updated",
                    "displays": manager.get_stage_displays()
                })
                # If a song is waiting in queue and nothing is playing, trigger countdown fresh on display connection!
                current_playing = get_current_playing()
                queue = get_queue()
                pending = [q for q in queue if q["status"] == "pending"]
                if not current_playing and pending and not current_intermission_end:
                    dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
                    current_intermission_end = time.time() + dur
                    await manager.broadcast({
                        "type": "start_intermission",
                        "next_track": pending[0],
                        "duration": dur
                    })
            elif msg_type == "stage_audio_state":
                disp_id = data.get("display_id")
                if disp_id and manager.is_display_authorized(websocket, disp_id):
                    manager.set_display_muted(disp_id, bool(data.get("is_muted", False)))
                    await manager.broadcast({
                        "type": "displays_updated",
                        "displays": manager.get_stage_displays()
                    })
            elif msg_type == "emoji_reaction":
                if manager.check_emoji_rate_limit(websocket):
                    await manager.broadcast({
                        "type": "emoji_reaction",
                        "emoji": data.get("emoji", "🌺")
                    })
            elif msg_type == "soundboard_play":
                if websocket in manager.host_connections:
                    await manager.broadcast({
                        "type": "soundboard_play",
                        "sound": data.get("sound", "applause")
                    })
            elif msg_type == "volume_change":
                if websocket in manager.host_connections:
                    await manager.broadcast({
                        "type": "volume_change",
                        "volume": data.get("volume", 1.0)
                    })
            elif msg_type == "pitch_change":
                if websocket in manager.host_connections:
                    await manager.broadcast({
                        "type": "pitch_change",
                        "pitch": data.get("pitch", 0)
                    })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast({
            "type": "displays_updated",
            "displays": manager.get_stage_displays()
        })
