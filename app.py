import os
import io
import time
import socket
import asyncio
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import qrcode

from config import (
    STATIC_DIR, TEMPLATES_DIR, TRACKS_DIR, HOST_TOKEN, HOST_PIN,
    PORT, DEFAULT_INTERMISSION_SECONDS, PUBLIC_HOST,
    load_config, save_config, is_configured
)
from database import (
    init_db, get_or_create_singer, get_singer_by_token,
    save_track, get_track_by_id, get_track_by_video_id,
    search_local_tracks, enqueue_song, get_queue,
    get_current_playing, set_queue_status, remove_from_queue,
    reorder_queue, get_singer_history, get_party_wrapped_stats,
    get_setting, set_setting, get_all_settings,
    get_singers_with_queue, remove_all_songs_by_singer,
    get_taken_avatars_for_name, purge_all_singers
)
from search import tiered_search
from downloader import (
    downloader_worker, enqueue_download, register_download_callback
)
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
    try:
        import urllib.request
        return urllib.request.urlopen("https://api.ipify.org", timeout=2).read().decode("utf-8").strip()
    except Exception:
        return get_local_ip()

LOCAL_IP = get_local_ip()
PUBLIC_IP = get_public_ip()

def get_base_url() -> str:
    host = get_public_host()
    if host:
        if not host.startswith("http://") and not host.startswith("https://"):
            return f"http://{host}"
        return host
    return f"http://{get_public_ip()}:{PORT}"

# WebSocket Connection Hub
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.stage_displays: Dict[str, Dict[str, Any]] = {}
        self.ws_to_display_id: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
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

# Background download completion notification
async def on_download_finished(track_id: int, local_path: Optional[str]):
    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue,
        "track_id": track_id,
        "ready": bool(local_path)
    })

register_download_callback(on_download_finished)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker_task = asyncio.create_task(downloader_worker())
    print("=" * 60)
    print("🌴 FLORIDA TIKI KARAOKE LOUNGE 🌴")
    print(f"👉 TV Main Stage Display: {get_base_url()}/stage")
    print(f"👉 Singer Mobile App:    {get_base_url()}/join")
    print(f"👉 Host DJ Remote URL:   {get_base_url()}/host?token={HOST_TOKEN}")
    print(f"👉 Host 4-Digit PIN:     {HOST_PIN}")
    print("=" * 60)
    yield
    worker_task.cancel()

app = FastAPI(title="Tiki Karaoke", lifespan=lifespan)
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

# Host Auth Helper
def is_host_request(request: Request) -> bool:
    cookie_token = request.cookies.get("tiki_host")
    if cookie_token == HOST_TOKEN:
        return True
    auth_header = request.headers.get("X-Host-Token")
    if auth_header == HOST_TOKEN:
        return True
    return False

# Page Routes
@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    cfg = load_config()
    return templates.TemplateResponse(request=request, name="setup.html", context={
        "config": cfg
    })

@app.post("/api/setup")
async def api_save_setup(payload: Dict[str, Any]):
    cfg = save_config(payload)
    if "bar_name" in payload:
        set_setting("bar_name", str(payload["bar_name"]))
    if "intermission_seconds" in payload:
        set_setting("intermission_seconds", str(payload["intermission_seconds"]))
    if "tts_enabled" in payload:
        set_setting("tts_enabled", "true" if payload["tts_enabled"] else "false")
    if "bgm_enabled" in payload:
        set_setting("bgm_enabled", "true" if payload["bgm_enabled"] else "false")
    return {"status": "ok", "redirect": "/stage", "config": cfg}

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
async def stage_page(request: Request):
    if not is_configured():
        return RedirectResponse(url="/setup")
    pub_h = get_public_host()
    return templates.TemplateResponse(request=request, name="host.html", context={
        "bar_name": get_setting("bar_name", "The Tiki Hut Karaoke Lounge"),
        "local_ip": LOCAL_IP,
        "public_ip": PUBLIC_IP,
        "base_url": get_base_url(),
        "display_ip": pub_h if pub_h else f"{PUBLIC_IP}:{PORT}",
        "port": PORT,
        "host_pin": HOST_PIN
    })

@app.get("/host")
async def host_pair_route(request: Request, token: str = ""):
    if token == HOST_TOKEN:
        response = RedirectResponse(url="/")
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 7, httponly=False)
        return response
    return HTMLResponse("<h3>Invalid Host Token!</h3><p>Scan the Host QR code from the main TV screen.</p>", status_code=403)

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
async def host_qr():
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

# Search API
@app.get("/api/search")
async def api_search(q: str = "", force_online: bool = False):
    return tiered_search(q, force_online=force_online)

# Singer API
@app.post("/api/singer/register")
async def api_register_singer(req: SingerRegisterRequest, request: Request):
    client_ip = request.client.host if request.client else ""
    try:
        singer = get_or_create_singer(
            token=req.token,
            name=req.name,
            avatar=req.avatar,
            ip_address=client_ip
        )
        return singer
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@app.get("/api/singer/available-avatars")
async def api_available_avatars(name: str = "", token: str = ""):
    taken = get_taken_avatars_for_name(name, exclude_token=token)
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

# Queue API
@app.get("/api/queue")
async def api_get_queue():
    return get_queue()

@app.post("/api/queue")
async def api_enqueue_song(req: QueueSongRequest, request: Request):
    client_ip = request.client.host if request.client else ""
    singer = get_singer_by_token(req.singer_token)
    if not singer:
        singer = get_or_create_singer(req.singer_token, "Guest", ip_address=client_ip)

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

    track = get_track_by_id(track_id)

    # Pre-cache / download track in background if not yet downloaded
    if track and not track["is_downloaded"]:
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

    # If nothing is currently playing and this is the only pending track, trigger intermission/play!
    if not current_playing:
        global current_intermission_end
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
async def api_play_queue_item(queue_id: int):
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
async def api_complete_queue_item(queue_id: int):
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

    return {"status": "completed"}

@app.get("/api/state")
async def api_get_state():
    current = get_current_playing()
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    next_up = pending[0] if pending else None
    
    intermission_remaining = 0
    if not current and next_up and current_intermission_end:
        intermission_remaining = max(0, int(current_intermission_end - time.time()))
    elif not current and next_up:
        intermission_remaining = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))

    return {
        "playing": current,
        "next": next_up,
        "queue": queue,
        "intermission_seconds": intermission_remaining
    }

# Host Controls & Settings
@app.get("/api/auth/role")
async def api_get_role(request: Request):
    return {"is_host": is_host_request(request)}

@app.get("/api/settings")
async def api_get_settings():
    return get_all_settings()

@app.post("/api/settings")
async def api_update_settings(settings: Dict[str, str]):
    for k, v in settings.items():
        set_setting(k, str(v))
    return {"status": "ok"}

@app.post("/api/host/skip")
async def host_skip_action(request: Request):
    await manager.broadcast({"type": "skip_track"})
    return {"status": "ok"}

@app.post("/api/host/toggle-pause")
async def host_pause_action(request: Request):
    await manager.broadcast({"type": "pause_track"})
    return {"status": "ok"}

@app.post("/api/host/start-now")
async def host_start_now_action(request: Request):
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    if pending:
        next_track = pending[0]
        await api_play_queue_item(next_track["queue_id"])
    return {"status": "ok"}

@app.post("/api/host/add-time")
async def host_add_time_action(request: Request):
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    await manager.broadcast({
        "type": "start_intermission",
        "next_track": pending[0] if pending else None,
        "duration": 30
    })
    return {"status": "ok"}

@app.get("/api/host/singers")
async def host_get_singers(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_singers_with_queue()

@app.post("/api/host/singer/{singer_id}/remove-all")
async def host_remove_all_singer_songs(singer_id: int, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    deleted_count = remove_all_songs_by_singer(singer_id)
    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })
    return {"status": "ok", "deleted_count": deleted_count}

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
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
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
            elif msg_type == "stage_audio_state":
                disp_id = data.get("display_id")
                if disp_id:
                    manager.set_display_muted(disp_id, bool(data.get("is_muted", False)))
                    await manager.broadcast({
                        "type": "displays_updated",
                        "displays": manager.get_stage_displays()
                    })
            elif msg_type == "emoji_reaction":
                await manager.broadcast({
                    "type": "emoji_reaction",
                    "emoji": data.get("emoji", "🌺")
                })
            elif msg_type == "soundboard_play":
                await manager.broadcast({
                    "type": "soundboard_play",
                    "sound": data.get("sound", "applause")
                })
            elif msg_type == "volume_change":
                await manager.broadcast({
                    "type": "volume_change",
                    "volume": data.get("volume", 1.0)
                })
            elif msg_type == "pitch_change":
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
