import json
import logging
import os
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app_lifecycle import (
    broadcast_network_update_sync,
    intermission_state,
    intermission_watchdog_loop,
    lifespan,
    main_event_loop,
)
from config import HOST_PIN, HOST_TOKEN, PORT, STATIC_DIR
from search import tiered_search
from services.auth import (
    STAGE_TOKEN,
    PinAuthRequest,
    failed_pin_attempts,
    get_active_room_code,
    is_host_request,
    is_local_request,
    is_room_code_required,
    is_stage_request,
)
from services.lifecycle import (
    get_focus_hub_trigger,
    get_launch_cdg_ripper_trigger,
    get_launch_importer_trigger,
    get_shutdown_trigger,
    get_tunnel_reload_trigger,
    register_focus_hub_trigger,
    register_launch_cdg_ripper_trigger,
    register_launch_importer_trigger,
    register_shutdown_trigger,
    register_tunnel_reload_trigger,
)
from services.network import (
    LOCAL_IP,
    PUBLIC_IP,
    get_base_url,
    get_join_url,
    get_local_ip,
    get_public_host,
    get_public_ip,
)
from services.playback import (
    background_metadata_enricher,
    extract_cdg_zip,
    get_local_media_duration,
    on_filler_track_ended,
    parse_karaoke_filename,
    trigger_filler_sequence,
    trigger_intermission_if_idle,
)
from services.temporary_cache import maybe_cleanup_temporary_track
from services.templates import templates
from services.websocket_manager import ConnectionManager, manager
from ws_handlers import dispatch_ws_message

logger = logging.getLogger("tiki.app")
current_intermission_end: Optional[float] = None

app = FastAPI(title="Tiki Karaoke", lifespan=lifespan)


@app.middleware("http")
async def add_security_and_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https" or os.getenv("TIKI_USE_HTTPS") == "1":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    if request.url.path.startswith("/static/") or request.url.path in ("/join", "/stage", "/host", "/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

from routes.host_controls import (
    AVSyncPayload,
    ImportLocalMediaRequest,
    MarqueeMessageRequest,
    MarqueeSpeedRequest,
    api_get_state,
    api_host_health,
    api_notify_on_deck,
    host_get_av_sync,
    host_get_ytdlp_status,
    host_set_av_sync,
    router as host_controls_router,
)
from routes.launcher import (
    find_importer_executable,
    find_rehearsal_lounge_executable,
    router as launcher_router,
)
from routes.library import (
    ConvertTrackTypeRequest,
    EditTrackMetadataRequest,
    PruneTracksRequest,
    router as library_router,
    search_requests_by_ip,
)
from routes.pages import router as pages_router
from routes.queue import (
    JoinDuetRequest,
    LeaveDuetRequest,
    QueueSongRequest,
    SingerRegisterRequest,
    SingerStatusRequest,
    last_queue_time_by_ip,
    last_queue_time_by_singer,
    router as queue_router,
)
from routes.recordings import HostUpdateRecordingsPathRequest, router as recordings_router
from routes.setup import router as setup_router
from routes.stream import router as stream_router

app.include_router(setup_router)
app.include_router(stream_router)
app.include_router(library_router)
app.include_router(queue_router)
app.include_router(pages_router)
app.include_router(launcher_router)
app.include_router(recordings_router)
app.include_router(host_controls_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
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
            raw_text = await websocket.receive_text()
            try:
                data = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"[WS] Malformed JSON payload from client: {e}")
                await websocket.send_json({"type": "ws_error", "message": "Malformed JSON message"})
                continue

            if not isinstance(data, dict):
                logger.warning(f"[WS] Expected JSON object, got {type(data).__name__}")
                await websocket.send_json({"type": "ws_error", "message": "WebSocket payload must be a JSON object"})
                continue

            await dispatch_ws_message(websocket, data, intermission_state)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast({
            "type": "displays_updated",
            "displays": manager.get_stage_displays(),
        })
