import asyncio
import os
import signal
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from audio_router import audio_router
from config import (
    DEFAULT_INTERMISSION_SECONDS,
    HOST_PIN,
    HOST_TOKEN,
    PORT,
    STATIC_DIR,
    get_db_path,
    get_tracks_dir,
)
from database import (
    clear_queue_on_startup,
    get_current_playing,
    get_pending_downloads,
    get_queue,
    get_setting,
    init_db,
    set_queue_status,
    set_setting,
)
from downloader import (
    downloader_worker,
    enqueue_download,
    filler_downloader_worker,
)
from search import tiered_search
import network_helper
from services.network import (
    LOCAL_IP,
    PUBLIC_IP,
    get_base_url,
    get_local_ip,
    get_public_host,
    get_public_ip,
)
from services.websocket_manager import ConnectionManager, manager

main_event_loop: Optional[asyncio.AbstractEventLoop] = None

def broadcast_network_update_sync():
    """Thread-safe synchronous helper to broadcast network_updated event across all connected WebSockets."""
    global main_event_loop
    if not main_event_loop or not main_event_loop.is_running():
        return
    effective_port = int(os.getenv("TIKI_PORT", str(PORT)))
    ip = network_helper.get_best_local_ip()
    is_hotspot = network_helper.is_hotspot_ip(ip)
    is_net = network_helper.is_internet_available()
    show_wifi_card = network_helper.is_hotspot_enabled()
    base_url = get_base_url()
    join_url = f"{base_url}/join"
    payload = {
        "type": "network_updated",
        "local_ip": ip,
        "port": effective_port,
        "base_url": base_url,
        "join_url": join_url,
        "is_internet_connected": is_net,
        "is_hotspot": is_hotspot,
        "hotspot_enabled": show_wifi_card,
        "hotspot_ssid": get_setting("hotspot_ssid", ""),
        "hotspot_password": get_setting("hotspot_password", ""),
        "show_wifi_card": show_wifi_card
    }
    asyncio.run_coroutine_threadsafe(manager.broadcast(payload), main_event_loop)

current_intermission_end: Optional[float] = None

from services.playback import (
    trigger_intermission_if_idle,
    trigger_filler_sequence,
    on_filler_track_ended,
    background_metadata_enricher,
    extract_cdg_zip,
    parse_karaoke_filename,
    get_local_media_duration,
)


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
    global main_event_loop
    main_event_loop = asyncio.get_running_loop()
    init_db()
    clear_queue_on_startup()
    # Restore persisted monitoring mode before any connections arrive
    saved_mode = get_setting("mic_monitoring_mode", "native")
    audio_router.set_monitoring_mode(saved_mode)
    worker_task = asyncio.create_task(downloader_worker())
    filler_worker_task = asyncio.create_task(filler_downloader_worker())
    watchdog_task = asyncio.create_task(intermission_watchdog_loop())
    enricher_task = asyncio.create_task(background_metadata_enricher())

    # Resume any staged downloads from SQLite
    try:
        pending_list = get_pending_downloads()
        for p in pending_list:
            if p.get("status") in ("pending", "downloading", "failed"):
                enqueue_download(
                    track_id=p["track_id"],
                    singer_name=p["singer_name"],
                    title=p["title"],
                    artist=p["artist"],
                    pending_id=p["id"],
                    quality_profile=p.get("quality_profile", "default")
                )
        if pending_list:
            print(f"[Startup] Resumed {len(pending_list)} staged downloads from database.")
    except Exception as e:
        print(f"[Startup] Error resuming pending downloads: {e}")
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
    enricher_task.cancel()

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
from services.templates import templates
from routes.setup import router as setup_router
from routes.stream import router as stream_router
from routes.library import (
    router as library_router,
    PruneTracksRequest, EditTrackMetadataRequest, ConvertTrackTypeRequest,
    search_requests_by_ip
)
from routes.queue import (
    router as queue_router,
    SingerRegisterRequest, QueueSongRequest, SingerStatusRequest,
    JoinDuetRequest, LeaveDuetRequest,
    last_queue_time_by_singer, last_queue_time_by_ip
)
from services.temporary_cache import maybe_cleanup_temporary_track

from routes.pages import router as pages_router
from routes.launcher import (
    router as launcher_router,
    find_importer_executable,
    find_rehearsal_lounge_executable,
)
from routes.recordings import (
    router as recordings_router,
    HostUpdateRecordingsPathRequest,
)
from routes.host_controls import (
    router as host_controls_router,
    api_get_state,
    api_host_health,
    host_get_ytdlp_status,
    host_get_av_sync,
    host_set_av_sync,
    AVSyncPayload,
    api_notify_on_deck,
    MarqueeMessageRequest,
    MarqueeSpeedRequest,
    ImportLocalMediaRequest,
)

app.include_router(setup_router)
app.include_router(stream_router)
app.include_router(library_router)
app.include_router(queue_router)
app.include_router(pages_router)
app.include_router(launcher_router)
app.include_router(recordings_router)
app.include_router(host_controls_router)

from services.auth import (
    STAGE_TOKEN, is_host_request, is_stage_request, is_local_request,
    PinAuthRequest, failed_pin_attempts
)
from services.lifecycle import (
    register_shutdown_trigger, register_focus_hub_trigger,
    register_launch_importer_trigger, register_launch_cdg_ripper_trigger,
    register_tunnel_reload_trigger, get_tunnel_reload_trigger,
    get_shutdown_trigger, get_focus_hub_trigger,
    get_launch_importer_trigger, get_launch_cdg_ripper_trigger
)


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
            if msg_type == "get_pending_downloads":
                await websocket.send_json({
                    "type": "pending_downloads_updated",
                    "pending_downloads": get_pending_downloads()
                })
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
            elif msg_type == "singer_joined":
                name = str(data.get("name", "Guest Singer")).strip()
                avatar = str(data.get("avatar", "🌴")).strip()
                if name:
                    await manager.broadcast({
                        "type": "singer_joined",
                        "name": name,
                        "avatar": avatar
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
            elif msg_type == "host_mic_settings":
                if websocket in manager.host_connections:
                    muted = data.get("muted")
                    volume = data.get("volume")
                    device = data.get("device")
                    monitoring_mode = data.get("monitoring_mode")
                    if muted is not None:
                        audio_router.set_muted(bool(muted))
                    if volume is not None:
                        audio_router.set_volume(int(volume))
                    if device is not None and str(device).strip():
                        audio_router.set_device(str(device).strip())
                    if monitoring_mode is not None:
                        from audio_router import MONITORING_MODES
                        if monitoring_mode in MONITORING_MODES:
                            audio_router.set_monitoring_mode(monitoring_mode)
                            set_setting("mic_monitoring_mode", monitoring_mode)
                            if monitoring_mode == "native" and not audio_router.is_muted:
                                audio_router.start_loopback()

                    if "reverb" in data and data["reverb"] is not None:
                        set_setting("mic_reverb", "true" if bool(data["reverb"]) else "false")
                    if "ducking_enabled" in data and data["ducking_enabled"] is not None:
                        set_setting("mic_ducking_enabled", "true" if bool(data["ducking_enabled"]) else "false")
                    if "ducking_db" in data and data["ducking_db"] is not None:
                        try:
                            set_setting("mic_ducking_db", str(int(float(data["ducking_db"]))))
                        except Exception:
                            pass
                    if "cdg_theme" in data and data["cdg_theme"]:
                        set_setting("cdg_theme", str(data["cdg_theme"]).strip())
                    if "cdg_glow" in data and data["cdg_glow"] is not None:
                        set_setting("cdg_glow", "true" if bool(data["cdg_glow"]) else "false")

                    st = audio_router.get_state()
                    try:
                        duck_db_val = int(float(get_setting("mic_ducking_db", "-6")))
                    except Exception:
                        duck_db_val = -6
                    msg = {
                        "type": "host_mic_settings",
                        "volume": st["volume"],
                        "muted": st["muted"],
                        "device": st["device"],
                        "available": st.get("available", False),
                        "latency_ms": st.get("latency_ms_est"),
                        "monitoring_mode": get_setting("mic_monitoring_mode", st.get("monitoring_mode", "native")),
                        "reverb": get_setting("mic_reverb", "true").lower() == "true",
                        "ducking_enabled": get_setting("mic_ducking_enabled", "true").lower() == "true",
                        "ducking_db": duck_db_val,
                        "cdg_theme": get_setting("cdg_theme", "neon"),
                        "cdg_glow": get_setting("cdg_glow", "true").lower() == "true"
                    }
                    await manager.broadcast(msg)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast({
            "type": "displays_updated",
            "displays": manager.get_stage_displays()
        })
