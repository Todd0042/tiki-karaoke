import os
import io
import re
import sys
import json
import time
import datetime
import socket
import asyncio
import secrets
import subprocess
import signal
import threading
import shutil
import tempfile
import urllib.parse
import urllib.request
import hashlib
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import qrcode

from config import (
    STATIC_DIR, TEMPLATES_DIR, TRACKS_DIR, HOST_TOKEN, HOST_PIN,
    PORT, DEFAULT_INTERMISSION_SECONDS, PUBLIC_HOST, REMOTE_MODE, BAR_NAME,
    load_config, save_config, is_configured, get_user_data_root,
    get_library_dir, get_tracks_dir, get_playlists_dir, get_recordings_dir, get_data_dir,
    get_db_path, get_path_info, ensure_storage_dirs, get_storage_status,
    get_available_displays, get_available_audio_sinks, get_available_audio_sources, get_bundled_binary_path,
    get_default_library_dir, get_default_data_dir, get_default_recordings_dir, find_system_browser, get_system_drives,
    get_available_browsers, open_url_in_preferred_browser
)
from database import (
    init_db, get_db, get_or_create_singer, get_singer_by_token,
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
    add_pending_download, get_pending_downloads, get_pending_download_by_id,
    get_pending_download_by_track_id, get_pending_download_by_filler_id,
    update_pending_download_progress, increment_pending_download_retry,
    remove_pending_download, promote_pending_download_to_queue,
    find_existing_downloaded_track,
    add_filler_track, get_filler_tracks, get_filler_track_by_id,
    get_filler_track_by_video_id, update_filler_track_status,
    update_filler_track_metadata, delete_filler_track,
    record_filler_play, get_next_vibe_filler_track, checkout_singer,
    set_track_sing_along, import_local_track, import_local_filler_track,
    set_singer_status, set_singer_status_by_token,
    save_recording, get_recordings_for_singer, get_all_recordings, delete_recording
)
from search import tiered_search, query_youtube_music_videos
from downloader import (
    downloader_worker, enqueue_download, register_download_callback,
    register_download_progress_callback,
    filler_downloader_worker, enqueue_filler_download, register_filler_callback
)
from metadata import fetch_track_metadata
from audio_router import audio_router
import network_helper
from playlist_sync import (
    is_authenticated as is_yt_authenticated,
    get_auth_url, exchange_code, sync_played_song_to_youtube
)

def get_local_ip() -> str:
    try:
        import network_helper
        return network_helper.get_best_local_ip()
    except Exception:
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

# BGM Filler Video State
filler_active: bool = False
filler_current_track: Optional[Dict[str, Any]] = None
filler_buffer_end: Optional[float] = None
filler_transition_state: str = "idle"  # 'idle', 'buffer_15s', 'playing', 'fading_out', 'finishing'
filler_buffer_task: Optional[asyncio.Task] = None

# Stage Dynamic Marquee State
active_marquee_message: Optional[str] = None
marquee_expires_at: Optional[float] = None
marquee_mode: str = "auto"  # "auto", "intermission_only", "off"
last_karaoke_track: Optional[Dict[str, Any]] = None

async def trigger_intermission_if_idle():
    """
    If nothing is currently playing and a stage display is connected, triggers smooth transition
    from ambient filler into intermission countdown for the next song in queue.
    """
    global current_intermission_end, filler_active, filler_transition_state, filler_buffer_task
    current_playing = get_current_playing()
    if current_playing:
        return

    queue = get_queue()
    if not queue:
        return

    # If an intermission countdown is already actively running, do not clobber it
    if current_intermission_end and time.time() < current_intermission_end:
        return

    next_song_item = queue[0]

    if filler_active:
        if filler_transition_state in ("fading_out", "finishing"):
            return
        elif filler_transition_state == "buffer_15s":
            if filler_buffer_task and not filler_buffer_task.done():
                filler_buffer_task.cancel()
            filler_active = False
            filler_transition_state = "idle"
        elif filler_transition_state == "playing":
            policy = get_setting("bgm_transition_policy", "fade")
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
                return
            else:
                filler_transition_state = "finishing"
                await manager.broadcast({
                    "type": "filler_transition_finish",
                    "next_singer": next_song_item["singer_name"] if next_song_item else "",
                    "next_title": next_song_item["title"] if next_song_item else "",
                    "next_artist": next_song_item["artist"] if next_song_item else ""
                })
                return

    dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
    current_intermission_end = time.time() + dur
    await manager.broadcast({
        "type": "start_intermission",
        "next_track": next_song_item,
        "duration": dur
    })

# Background download completion notifications
async def on_download_finished(track_id: int, local_path: Optional[str], pending_id: Optional[int] = None):
    was_pending_request = pending_id is not None
    pending = None
    if pending_id:
        pending = get_pending_download_by_id(pending_id)
    if not pending:
        pending = get_pending_download_by_track_id(track_id)
        if pending:
            pending_id = pending["id"]

    if local_path:
        if pending and pending_id:
            promote_pending_download_to_queue(pending_id)
        elif was_pending_request and not pending:
            # Download was cancelled by singer/host while yt-dlp was in-flight
            maybe_cleanup_temporary_track(track_id)

        queue = get_queue()
        await manager.broadcast({
            "type": "queue_updated",
            "queue": queue,
            "track_id": track_id,
            "ready": True
        })
        await manager.broadcast({
            "type": "pending_downloads_updated",
            "pending_downloads": get_pending_downloads()
        })
        await trigger_intermission_if_idle()
    else:
        if pending_id and pending:
            rc = pending.get("retry_count", 0)
            if rc == 0:
                # Fail 1: auto-retry once silently in background
                increment_pending_download_retry(pending_id)
                enqueue_download(
                    track_id=track_id,
                    singer_name=pending["singer_name"],
                    title=pending["title"],
                    artist=pending["artist"],
                    pending_id=pending_id,
                    quality_profile=pending.get("quality_profile", "default")
                )
                await manager.broadcast({
                    "type": "pending_downloads_updated",
                    "pending_downloads": get_pending_downloads()
                })
            elif rc == 1:
                # Fail 2: host alert & retry button
                increment_pending_download_retry(pending_id)
                update_pending_download_progress(
                    pending_id, 0.0, status="failed",
                    error_message="Download stalled. Tap Retry to try again."
                )
                await manager.broadcast({
                    "type": "pending_downloads_updated",
                    "pending_downloads": get_pending_downloads()
                })
                await manager.broadcast({
                    "type": "host_alert",
                    "level": "warning",
                    "message": f"Download stalled for {pending['singer_name']} ({pending['title']})",
                    "pending_id": pending_id
                })
            else:
                # Fail 3+: offer medium/lowest quality & search another version
                increment_pending_download_retry(pending_id)
                update_pending_download_progress(
                    pending_id, 0.0, status="failed",
                    error_message="Download repeatedly failed. Try medium or lowest quality."
                )
                await manager.broadcast({
                    "type": "pending_downloads_updated",
                    "pending_downloads": get_pending_downloads()
                })
        else:
            await manager.broadcast({
                "type": "pending_downloads_updated",
                "pending_downloads": get_pending_downloads()
            })

register_download_callback(on_download_finished)

async def on_download_progress(track_id: int, pending_id: Optional[int], pct: float, status: str):
    await manager.broadcast({
        "type": "download_progress",
        "track_id": track_id,
        "pending_id": pending_id,
        "progress": pct,
        "status": status,
        "pending_downloads": get_pending_downloads()
    })

register_download_progress_callback(on_download_progress)

async def on_filler_download_finished(filler_id: int, local_path: Optional[str], pending_id: Optional[int] = None):
    track = get_filler_track_by_id(filler_id)
    if not pending_id:
        pending = get_pending_download_by_filler_id(filler_id)
        if pending:
            pending_id = pending["id"]

    if local_path:
        if pending_id:
            remove_pending_download(pending_id)
    else:
        if pending_id:
            update_pending_download_progress(pending_id, 0.0, "failed", "Download failed")

    await manager.broadcast({
        "type": "filler_track_updated",
        "track": track,
        "filler_id": filler_id,
        "ready": bool(local_path)
    })
    await manager.broadcast({
        "type": "pending_downloads_updated",
        "pending_downloads": get_pending_downloads()
    })

register_filler_callback(on_filler_download_finished)

async def trigger_filler_sequence(last_artist: str = "", last_genre: str = "", last_year: int = 0, is_paused: bool = False):
    global filler_active, filler_transition_state, filler_buffer_end, filler_buffer_task, filler_current_track
    if get_setting("bgm_video_enabled", "true") != "true":
        return

    vibe_mode = get_setting("intermission_vibe_mode", "sing_along")
    next_track = get_next_vibe_filler_track(last_artist, last_genre, last_year, mode=vibe_mode, is_paused=is_paused)
    if not next_track:
        return

    # If queue is empty or all pending singers are on break, start downloaded music video immediately without text countdown
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending" and q.get("singer_status", "active") == "active"]
    if not pending or is_paused:
        filler_active = True
        filler_transition_state = "playing"
        filler_current_track = next_track
        filler_buffer_end = None
        record_filler_play(next_track["id"], is_sing_along=bool(next_track.get("is_sing_along")))
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
                    pick = get_next_vibe_filler_track(last_artist, last_genre, last_year, mode=vibe_mode, is_paused=is_paused)
                    if pick:
                        filler_current_track = pick
                        filler_transition_state = "playing"
                        filler_buffer_end = None
                        record_filler_play(pick["id"], is_sing_along=bool(pick.get("is_sing_along")))
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
    pending = [q for q in queue if q["status"] == "pending" and q.get("singer_status", "active") == "active"]

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
        vibe_mode = get_setting("intermission_vibe_mode", "sing_along")
        next_track = get_next_vibe_filler_track(last_artist, last_genre, mode=vibe_mode, is_paused=False)
        if next_track:
            filler_current_track = next_track
            filler_transition_state = "playing"
            record_filler_play(next_track["id"], is_sing_along=bool(next_track.get("is_sing_along")))
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

async def background_metadata_enricher():
    """
    Background worker that gently enriches tracks & filler tracks missing genre or release_year.
    """
    await asyncio.sleep(4)  # Let server boot and stabilize
    try:
        from database import get_db, update_track_metadata, update_filler_track_metadata
        from metadata import fetch_track_metadata

        conn = get_db()
        missing_tracks = conn.execute("""
            SELECT id, title, artist, is_sing_along FROM tracks
            WHERE (genre = '' OR genre IS NULL OR release_year = 0 OR release_year IS NULL)
              AND is_downloaded = 1
            LIMIT 150
        """).fetchall()
        missing_tracks = [dict(r) for r in missing_tracks]

        missing_filler = conn.execute("""
            SELECT id, title, artist FROM filler_tracks
            WHERE (genre = '' OR genre IS NULL OR release_year = 0 OR release_year IS NULL)
            LIMIT 150
        """).fetchall()
        missing_filler = [dict(r) for r in missing_filler]
        conn.close()

        loop = asyncio.get_event_loop()
        updated_count = 0

        for t in missing_tracks:
            try:
                meta = await loop.run_in_executor(None, fetch_track_metadata, t["artist"], t["title"])
                if meta and (meta.get("genre") or meta.get("release_year")):
                    update_track_metadata(t["id"], genre=meta.get("genre", ""), release_year=meta.get("release_year", 0))
                    updated_count += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[Metadata Enricher] Error for track {t.get('id')}: {e}")

        for f in missing_filler:
            try:
                meta = await loop.run_in_executor(None, fetch_track_metadata, f["artist"], f["title"])
                if meta and (meta.get("genre") or meta.get("release_year")):
                    update_filler_track_metadata(f["id"], genre=meta.get("genre", "Pop"), release_year=meta.get("release_year", 0))
                    updated_count += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[Metadata Enricher] Error for filler {f.get('id')}: {e}")

        if updated_count > 0:
            print(f"[Metadata Enricher] Auto-enriched metadata for {updated_count} tracks.")
            await manager.broadcast({"type": "library_updated"})
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[Metadata Enricher] Task exception: {e}")

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

app.include_router(setup_router)
app.include_router(stream_router)
app.include_router(library_router)
app.include_router(queue_router)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    icon_path = STATIC_DIR / "favicon.ico"
    if icon_path.exists():
        return FileResponse(icon_path, media_type="image/x-icon")
    svg_path = STATIC_DIR / "tiki-icon.svg"
    if svg_path.exists():
        return FileResponse(svg_path, media_type="image/svg+xml")
    return Response(status_code=204)

# Request Models


class MarqueeMessageRequest(BaseModel):
    message: str
    duration_minutes: Optional[int] = 0
    mode: Optional[str] = "auto"
    speed: Optional[int] = None

class MarqueeSpeedRequest(BaseModel):
    speed: int

class HostUpdateRecordingsPathRequest(BaseModel):
    recordings_dir: str = ""

from services.auth import (
    STAGE_TOKEN, is_host_request, is_stage_request, is_local_request,
    PinAuthRequest, failed_pin_attempts
)

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
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 30, httponly=False, samesite="lax")
        return response
    else:
        attempts.append(now)
        failed_pin_attempts[client_ip] = attempts
        raise HTTPException(status_code=401, detail="Invalid Host PIN. Please check the TV display.")








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
    import network_helper
    show_wifi_card = network_helper.is_hotspot_enabled()
    hotspot_ssid = network_helper.get_hotspot_ssid()
    hotspot_password = network_helper.get_hotspot_password()
    response = templates.TemplateResponse(request=request, name="host.html", context={
        "bar_name": get_setting("bar_name", "The Tiki Hut Karaoke Lounge"),
        "local_ip": LOCAL_IP,
        "public_ip": PUBLIC_IP,
        "base_url": get_base_url(),
        "display_ip": pub_h if pub_h else f"{PUBLIC_IP}:{PORT}",
        "port": PORT,
        "host_pin": pin,
        "show_wifi_card": show_wifi_card,
        "hotspot_ssid": hotspot_ssid,
        "hotspot_password": hotspot_password
    })
    response.set_cookie("tiki_stage", STAGE_TOKEN, max_age=86400 * 30, httponly=False)
    if (token and token == HOST_TOKEN) or is_host_request(request) or is_local_request(request):
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 30, httponly=False, samesite="lax")
    return response

@app.get("/host")
async def host_pair_route(request: Request, token: str = ""):
    if (token and token == HOST_TOKEN) or is_host_request(request) or is_local_request(request):
        from urllib.parse import urlencode
        q_params = dict(request.query_params)
        q_params["tab"] = "host"
        redirect_url = f"/?{urlencode(q_params)}"
        response = RedirectResponse(url=redirect_url)
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 7, httponly=False, samesite="lax")
        return response
    return HTMLResponse("<h3>Invalid Host Token!</h3><p>Scan the Host QR code from the main TV screen or enter the Host PIN on the singer screen.</p>", status_code=403)

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

@app.get("/legal", response_class=HTMLResponse)
async def legal_guide_page(request: Request):
    cfg = load_config()
    default_bar = cfg.get("bar_name") or BAR_NAME or "The Tiki Hut Karaoke Lounge"
    bar_name = get_setting("bar_name", default_bar)
    return templates.TemplateResponse(
        request=request,
        name="legal.html",
        context={
            "bar_name": bar_name,
            "base_url": get_base_url()
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

@app.get("/qr/wifi")
async def wifi_qr():
    import network_helper
    ssid = network_helper.get_hotspot_ssid()
    password = network_helper.get_hotspot_password()
    wifi_str = network_helper.format_wifi_qr_string(ssid, password)
    png_bytes = network_helper.generate_qr_png_bytes(wifi_str)
    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")

@app.get("/qr/host")
async def host_qr(request: Request):
    if not (is_host_request(request) or is_stage_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required to view host QR code.")
    url = f"{get_base_url()}/host?token={HOST_TOKEN}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")






@app.post("/api/queue/{queue_id}/play")
async def api_play_queue_item(queue_id: int, request: Request):
    if not is_host_request(request) and not is_local_request(request) and not is_stage_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global current_intermission_end
    current_intermission_end = None
    prev_playing = get_current_playing()
    set_queue_status(queue_id, "playing")
    if prev_playing and prev_playing.get("queue_id") != queue_id:
        maybe_cleanup_temporary_track(prev_playing.get("track_id"))
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
    if not is_host_request(request) and not is_local_request(request) and not is_stage_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global current_intermission_end
    queue_list = get_queue()
    item = next((q for q in queue_list if q["queue_id"] == queue_id), None)

    set_queue_status(queue_id, "completed")
    if item:
        maybe_cleanup_temporary_track(item.get("track_id"))

    # Sync to YouTube playlist in background if authenticated (online YouTube tracks only)
    if item and is_yt_authenticated():
        vid = str(item.get("video_id") or "")
        if vid and not vid.startswith("local_") and item.get("media_source") != "imported":
            asyncio.create_task(asyncio.to_thread(
                sync_played_song_to_youtube, item["singer_name"], vid
            ))

    remaining = get_queue()
    active_pending = [q for q in remaining if q["status"] == "pending" and q.get("singer_status", "active") == "active"]
    next_up = active_pending[0] if active_pending else None

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
    active_pending = [q for q in pending if q.get("singer_status", "active") == "active"]
    next_up = active_pending[0] if active_pending else None
    stage_displays = manager.get_stage_displays()
    
    intermission_remaining = 0
    if not current and next_up and current_intermission_end:
        intermission_remaining = max(0, int(current_intermission_end - time.time()))
    elif not current and next_up:
        intermission_remaining = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))

    # Prefer downloaded BGM videos when stage is idle and active queue is empty
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

    # Check marquee message expiry
    global active_marquee_message, marquee_expires_at, marquee_mode
    if marquee_expires_at and time.time() > marquee_expires_at:
        active_marquee_message = None
        marquee_expires_at = None

    queue_summary = []
    if current:
        queue_summary.append(f"NOW: {current.get('singer_name', 'Singer')} - {current.get('title', 'Song')}")
    if next_up:
        queue_summary.append(f"UP NEXT: {next_up.get('singer_name', 'Singer')} - {next_up.get('title', 'Song')}")
    if len(active_pending) > 1:
        on_deck = active_pending[1]
        queue_summary.append(f"ON DECK: {on_deck.get('singer_name', 'Singer')}")
    if active_pending:
        queue_summary.append(f"({len(active_pending)} in queue)")

    queue_ticker_str = "  •  ".join(queue_summary) if queue_summary else "OPEN MIC: Scan QR to Sing!"

    return {
        "playing": current,
        "next": next_up,
        "queue": queue,
        "marquee": {
            "message": active_marquee_message,
            "expires_at": marquee_expires_at,
            "mode": marquee_mode,
            "queue_ticker": queue_ticker_str,
            "current_dedication": current.get("dedication", "") if current else "",
            "speed": int(get_setting("marquee_scroll_speed", "85"))
        },
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
            "ffmpeg_installed": bool(shutil.which("ffmpeg") or get_bundled_binary_path("ffmpeg").exists()),
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
        "ffmpeg_installed": bool(shutil.which("ffmpeg") or get_bundled_binary_path("ffmpeg").exists()),
        "ffprobe_installed": bool(shutil.which("ffprobe") or get_bundled_binary_path("ffprobe").exists()),
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

@app.get("/api/host/mic-state")
async def api_get_host_mic_state():
    st = audio_router.get_state()
    # Persist monitoring_mode and DSP/CDG settings so they survive restarts
    st["monitoring_mode"] = get_setting("mic_monitoring_mode", st.get("monitoring_mode", "native"))
    st["reverb"] = get_setting("mic_reverb", "true").lower() == "true"
    st["ducking_enabled"] = get_setting("mic_ducking_enabled", "true").lower() == "true"
    try:
        st["ducking_db"] = int(float(get_setting("mic_ducking_db", "-6")))
    except Exception:
        st["ducking_db"] = -6
    st["cdg_theme"] = get_setting("cdg_theme", "neon")
    st["cdg_glow"] = get_setting("cdg_glow", "true").lower() == "true"
    return JSONResponse(st)


@app.post("/api/host/mic-monitoring-mode")
async def api_set_mic_monitoring_mode(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    data = await request.json()
    mode = str(data.get("mode", "native")).strip()
    from audio_router import MONITORING_MODES
    if mode not in MONITORING_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode '{mode}'. Valid: {MONITORING_MODES}")
    audio_router.set_monitoring_mode(mode)
    set_setting("mic_monitoring_mode", mode)
    if mode == "native" and not audio_router.is_muted:
        audio_router.start_loopback()
    st = audio_router.get_state()
    msg = {
        "type": "host_mic_settings",
        "monitoring_mode": mode,
        "available": st.get("available", False),
        "muted": st.get("muted", True),
        "volume": st.get("volume", 80),
        "device": st.get("device", "default"),
        "latency_ms": st.get("latency_ms_est"),
    }
    await manager.broadcast(msg)
    return {"status": "ok", "monitoring_mode": mode}

@app.get("/api/bgm-tracks")
async def api_get_bgm_tracks():
    bgm_dir = STATIC_DIR / "bgm"
    if not bgm_dir.exists():
        return {"tracks": []}
    tracks = sorted([f"/static/bgm/{f.name}" for f in bgm_dir.glob("*.mp3")])
    return {"tracks": tracks}


@app.post("/api/host/skip")
async def host_skip_action(request: Request):
    if not is_host_request(request) and not is_local_request(request) and not is_stage_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global current_intermission_end, filler_active, filler_transition_state, filler_current_track, filler_buffer_end
    current = get_current_playing()
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]

    if current:
        set_queue_status(current["queue_id"], "skipped")
        maybe_cleanup_temporary_track(current.get("track_id"))
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
            if get_setting("bgm_video_enabled", "true") == "true":
                last_artist = current.get("artist", "")
                await trigger_filler_sequence(last_artist=last_artist)
        await manager.broadcast({"type": "queue_updated", "queue": queue})
        return {"status": "ok"}

    elif pending:
        skipped_track_id = pending[0].get("track_id")
        set_queue_status(pending[0]["queue_id"], "skipped")
        maybe_cleanup_temporary_track(skipped_track_id)
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
            if get_setting("bgm_video_enabled", "true") == "true":
                await trigger_filler_sequence()
        await manager.broadcast({"type": "queue_updated", "queue": queue})
        return {"status": "ok"}

    else:
        # No karaoke track playing AND no tracks in queue:
        # Host requested skipping to next filler/background song!
        bgm_video_on = get_setting("bgm_video_enabled", "true") == "true"
        current_id = filler_current_track.get("id") if filler_current_track else None
        last_artist = filler_current_track.get("artist", "") if filler_current_track else ""
        last_genre = filler_current_track.get("genre", "") if filler_current_track else ""
        last_year = filler_current_track.get("release_year", 0) if filler_current_track else 0
        vibe_mode = get_setting("intermission_vibe_mode", "sing_along")

        next_filler = None
        if bgm_video_on:
            next_filler = get_next_vibe_filler_track(
                last_track_artist=last_artist,
                last_track_genre=last_genre,
                last_track_year=last_year,
                mode=vibe_mode,
                is_paused=False,
                exclude_track_id=current_id
            )

        if next_filler:
            filler_current_track = next_filler
            filler_active = True
            filler_transition_state = "playing"
            filler_buffer_end = None
            record_filler_play(next_filler["id"], is_sing_along=bool(next_filler.get("is_sing_along")))
            await manager.broadcast({
                "type": "play_filler_track",
                "track": next_filler
            })
            return {"status": "ok", "skipped_to_filler": f"{next_filler.get('artist', '')} - {next_filler.get('title', '')}"}
        else:
            # Fallback when no filler music videos are available: advance background audio
            await manager.broadcast({"type": "skip_bgm"})
            return {"status": "ok", "skipped_to_bgm": True}

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
        await trigger_filler_sequence(is_paused=True)

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
    return {"status": "ok"}


def parse_karaoke_filename(filename: str) -> Tuple[str, str]:
    stem = Path(filename).stem
    cleaned = re.sub(r'[\(\[\{]\s*(?:karaoke|instrumental|lyrics|karaoke version|sing-along)\s*[\)\]\}]', '', stem, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'^\d+[\s\.\-_]+', '', cleaned).strip()
    for delim in (' - ', ' _ ', ' – ', ' — ', ' ~ '):
        if delim in cleaned:
            parts = cleaned.split(delim, 1)
            artist = parts[0].strip()
            title = parts[1].strip()
            if artist and title:
                return artist, title
    return "Karaoke Track", cleaned or stem

def get_local_media_duration(file_path: str) -> int:
    ffprobe_bin = "ffprobe" if shutil.which("ffprobe") else str(get_bundled_binary_path("ffprobe"))
    if not (shutil.which("ffprobe") or os.path.exists(ffprobe_bin)):
        return 210
    try:
        cmd = [
            ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
        return int(float(res.stdout.strip()))
    except Exception:
        return 210

@app.get("/api/host/default-paths")
async def api_get_default_paths(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    
    lib_dir = get_library_dir()
    default_lib = get_default_library_dir()
    karaoke_parent = default_lib.parent
    data_dir = get_data_dir()
    
    return {
        "status": "ok",
        "current_library_dir": str(lib_dir),
        "default_music_dir": str(default_lib),
        "user_karaoke_parent": str(karaoke_parent),
        "db_dir": str(data_dir),
        "is_windows": sys.platform == "win32"
    }

class ImportLocalMediaRequest(BaseModel):
    folder_path: str
    recursive: bool = True
    is_sing_along: bool = False
    target_destination: str = "karaoke"  # "karaoke", "sing_along", "filler"

def extract_cdg_zip(zip_path: Path) -> Optional[Tuple[str, str]]:
    """Inspects a zip archive. If it contains a .cdg and an audio file, extracts them to persistent cdg_cache."""
    try:
        cache_dir = get_user_data_root() / "cdg_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            cdg_names = [n for n in names if n.lower().endswith(".cdg") and not n.startswith("__MACOSX") and not Path(n).name.startswith(".")]
            audio_names = [n for n in names if Path(n).suffix.lower() in {".mp3", ".ogg", ".wav", ".m4a", ".flac"} and not n.startswith("__MACOSX") and not Path(n).name.startswith(".")]
            if cdg_names and audio_names:
                h = hashlib.sha256(str(zip_path.resolve()).encode()).hexdigest()[:12]
                out_dir = cache_dir / h
                out_dir.mkdir(parents=True, exist_ok=True)
                audio_entry = audio_names[0]
                cdg_entry = cdg_names[0]
                extracted_audio = out_dir / Path(audio_entry).name
                extracted_cdg = out_dir / Path(cdg_entry).name
                with open(extracted_audio, "wb") as f_out, zf.open(audio_entry) as f_in:
                    shutil.copyfileobj(f_in, f_out)
                with open(extracted_cdg, "wb") as f_out, zf.open(cdg_entry) as f_in:
                    shutil.copyfileobj(f_in, f_out)
                return str(extracted_audio), str(extracted_cdg)
    except Exception as e:
        print(f"[CDG] Error extracting zip {zip_path.name}: {e}")
    return None

@app.post("/api/host/import-local-library")
async def api_import_local_library(payload: ImportLocalMediaRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    folder_str = payload.folder_path.strip()
    if not folder_str:
        raise HTTPException(status_code=400, detail="Folder path cannot be empty")

    folder = Path(folder_str).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist or is not a folder: {folder_str}")

    VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v"}
    AUDIO_EXTS = {".mp3", ".ogg", ".wav", ".m4a", ".flac"}
    CDG_EXTS = {".cdg"}
    ZIP_EXTS = {".zip"}
    ALL_VALID = VIDEO_EXTS | AUDIO_EXTS | CDG_EXTS | ZIP_EXTS

    if payload.recursive:
        all_files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in ALL_VALID]
    else:
        all_files = [p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in ALL_VALID]

    if not all_files:
        return {
            "status": "ok",
            "imported_count": 0,
            "total_found": 0,
            "message": "No supported karaoke files found in folder (.mp4, .zip, .mp3+cdg, .mkv, .webm)"
        }

    imported = []
    is_filler = (payload.target_destination == "filler")
    is_sa = payload.is_sing_along or (payload.target_destination == "sing_along")

    processed_audio_paths = set()
    cdg_items = []

    # 1. Inspect .zip archives for MP3+CDG
    for p in all_files:
        if p.suffix.lower() == ".zip":
            extracted = extract_cdg_zip(p)
            if extracted:
                audio_p, cdg_p = extracted
                cdg_items.append((p.name, audio_p, cdg_p))

    # 2. Inspect uncompressed .cdg pairs
    for p in all_files:
        if p.suffix.lower() == ".cdg":
            parent = p.parent
            stem = p.stem
            found_audio = None
            for a_ext in (".mp3", ".ogg", ".wav", ".m4a", ".flac"):
                cand = parent / f"{stem}{a_ext}"
                if cand.exists():
                    found_audio = cand
                    break
            if found_audio:
                audio_res = str(found_audio.resolve())
                processed_audio_paths.add(audio_res)
                cdg_items.append((p.name, audio_res, str(p.resolve())))

    # Import CDG tracks
    for display_name, audio_p, cdg_p in cdg_items:
        artist, title = parse_karaoke_filename(display_name)
        dur = get_local_media_duration(audio_p)
        try:
            tid = import_local_track(
                file_path=audio_p,
                title=title,
                artist=artist,
                duration=dur,
                is_sing_along=is_sa,
                media_type="cdg",
                cdg_path=cdg_p
            )
            imported.append({"id": tid, "title": title, "artist": artist, "file": display_name, "type": "cdg"})
        except Exception as e:
            print(f"[Import] Error importing CDG {display_name}: {e}")

    # 3. Import regular video files (and standalone audio if filler)
    for f in all_files:
        ext = f.suffix.lower()
        res_str = str(f.resolve())
        if ext in VIDEO_EXTS or (ext in AUDIO_EXTS and res_str not in processed_audio_paths and is_filler):
            artist, title = parse_karaoke_filename(f.name)
            dur = get_local_media_duration(res_str)
            try:
                if is_filler:
                    fid = import_local_filler_track(
                        file_path=res_str,
                        title=title,
                        artist=artist,
                        duration=dur
                    )
                    imported.append({"id": fid, "title": title, "artist": artist, "file": f.name, "type": "filler"})
                else:
                    tid = import_local_track(
                        file_path=res_str,
                        title=title,
                        artist=artist,
                        duration=dur,
                        is_sing_along=is_sa,
                        media_type="video"
                    )
                    imported.append({"id": tid, "title": title, "artist": artist, "file": f.name, "type": "sing_along" if is_sa else "karaoke"})
            except Exception as e:
                print(f"[Import] Error importing {f.name}: {e}")

    if is_filler:
        await manager.broadcast({"type": "filler_tracks_updated"})
    else:
        await manager.broadcast({"type": "library_updated"})

    dest_label = "intermission filler crate" if is_filler else ("sing-along library" if is_sa else "local karaoke library")
    return {
        "status": "ok",
        "imported_count": len(imported),
        "total_found": len(all_files),
        "target_destination": payload.target_destination,
        "message": f"Successfully imported {len(imported)} media tracks into {dest_label}!",
        "tracks": imported[:50]
    }


def find_importer_executable() -> Optional[Tuple[List[str], Optional[Path]]]:
    """
    Locates the Tiki Karaoke Smart Importer executable or script.
    Returns (cmd_args, working_dir) or None.
    """
    current_dir = Path(__file__).resolve().parent

    # 1. Bundled in same directory or bin (Windows .exe or Linux binary)
    if sys.platform == "win32":
        candidates = [
            current_dir / "TikiKaraokeImporter.exe",
            current_dir / "bin" / "TikiKaraokeImporter.exe",
            current_dir.parent / "TikiKaraokeImporter.exe",
            current_dir.parent / "bin" / "TikiKaraokeImporter.exe",
        ]
        for c in candidates:
            if c.is_file():
                return ([str(c)], c.parent)
    else:
        candidates = [
            current_dir / "TikiKaraokeImporter",
            current_dir.parent / "TikiKaraokeImporter",
            current_dir / "bin" / "TikiKaraokeImporter",
            current_dir.parent / "bin" / "TikiKaraokeImporter",
            Path("/usr/bin/TikiKaraokeImporter"),
        ]
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                return ([str(c)], c.parent)
        for c in sorted(current_dir.glob("TikiKaraokeImporter*.AppImage"), key=lambda p: p.stat().st_mtime, reverse=True):
            if c.is_file() and os.access(c, os.X_OK):
                return ([str(c)], c.parent)

    # 2. If running inside a standalone bundle (PyInstaller / AppImage), self-invoke with --importer
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        if exe_path.is_file():
            return ([str(exe_path), "--importer"], exe_path.parent)

    # 3. Check internal merged module in development mode: current_dir / "importer" / "main.py"
    internal_importer = current_dir / "importer"
    if (internal_importer / "main.py").is_file():
        return ([sys.executable, str(internal_importer / "main.py")], internal_importer)
    if (current_dir.parent / "importer" / "main.py").is_file():
        return ([sys.executable, str(current_dir.parent / "importer" / "main.py")], current_dir.parent / "importer")

    # 4. Check sibling repository: ../tiki-karaoke-importer
    importer_repo = current_dir.parent / "tiki-karaoke-importer"
    if importer_repo.is_dir():
        if sys.platform == "win32":
            win_exe = importer_repo / "dist" / "windows" / "TikiKaraokeImporter.exe"
            if win_exe.is_file():
                return ([str(win_exe)], win_exe.parent)
        else:
            for appimage in sorted((importer_repo / "dist").glob("TikiKaraokeImporter*.AppImage"), key=lambda p: p.stat().st_mtime, reverse=True):
                if appimage.is_file() and os.access(appimage, os.X_OK):
                    return ([str(appimage)], appimage.parent)

        main_py = importer_repo / "main.py"
        if main_py.is_file():
            return ([sys.executable, str(main_py)], importer_repo)

    return None


def find_rehearsal_lounge_executable() -> Optional[Tuple[List[str], Optional[Path]]]:
    """
    Locates the Tiki Rehearsal Lounge executable, AppImage, or development script.
    Returns (cmd_args, working_dir) or None.
    """
    current_dir = Path(__file__).resolve().parent

    # 1. Bundled or local binaries (Windows .exe or Linux binary)
    if sys.platform == "win32":
        candidates = [
            current_dir / "TikiRehearsalLounge.exe",
            current_dir / "bin" / "TikiRehearsalLounge.exe",
            current_dir.parent / "TikiRehearsalLounge.exe",
            current_dir.parent / "bin" / "TikiRehearsalLounge.exe",
        ]
        for c in candidates:
            if c.is_file():
                return ([str(c)], c.parent)
    else:
        # Check PATH and system locations
        for bin_name in ["tiki-rehearsal-lounge", "TikiRehearsalLounge"]:
            which_path = shutil.which(bin_name)
            if which_path and os.access(which_path, os.X_OK):
                p = Path(which_path)
                return ([str(p)], p.parent)

        direct_bins = [
            current_dir / "TikiRehearsalLounge",
            current_dir / "bin" / "TikiRehearsalLounge",
            Path.home() / ".local" / "bin" / "tiki-rehearsal-lounge",
            Path("/usr/local/bin/tiki-rehearsal-lounge"),
            Path("/usr/bin/tiki-rehearsal-lounge"),
        ]
        for b in direct_bins:
            if b.is_file() and os.access(b, os.X_OK):
                return ([str(b)], b.parent)

        # Check AppImages in current, parent, and Applications directories
        appimage_dirs = [
            current_dir,
            current_dir.parent,
            Path.home() / "Applications",
            Path.home() / "Desktop",
            Path.home() / "Downloads",
        ]
        for search_dir in appimage_dirs:
            if not search_dir.is_dir():
                continue
            for pattern in ["*Rehearsal*.AppImage", "*rehearsal*.AppImage", "TikiRehearsalLounge*.AppImage"]:
                for appimage in sorted(search_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
                    if appimage.is_file() and os.access(appimage, os.X_OK):
                        return ([str(appimage)], appimage.parent)

    # 2. Check sibling repository: ../tiki-rehearsal-lounge
    rehearsal_repo = current_dir.parent / "tiki-rehearsal-lounge"
    if rehearsal_repo.is_dir():
        if sys.platform == "win32":
            win_exe = rehearsal_repo / "dist" / "windows" / "TikiRehearsalLounge.exe"
            if win_exe.is_file():
                return ([str(win_exe)], win_exe.parent)
        else:
            dist_dir = rehearsal_repo / "dist"
            if dist_dir.is_dir():
                for pattern in ["*Rehearsal*.AppImage", "*rehearsal*.AppImage", "TikiRehearsalLounge*.AppImage"]:
                    for appimage in sorted(dist_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
                        if appimage.is_file() and os.access(appimage, os.X_OK):
                            return ([str(appimage)], appimage.parent)

        main_py = rehearsal_repo / "main.py"
        if main_py.is_file():
            return ([sys.executable, str(main_py)], rehearsal_repo)

    return None



@app.post("/api/host/launch-importer")
async def api_launch_importer(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    cb = get_launch_importer_trigger()
    if cb:
        try:
            cb()
            return {
                "status": "ok",
                "message": "Tiki Karaoke Smart Importer opened successfully on desktop"
            }
        except Exception as e:
            print(f"[API] Error invoking importer desktop trigger: {e}")

    found = find_importer_executable()
    if not found:
        raise HTTPException(
            status_code=404,
            detail="Tiki Karaoke Smart Importer executable not found. Please ensure TikiKaraokeImporter is installed."
        )

    cmd_base, work_dir = found
    db_path = str(get_db_path().resolve()) if hasattr(get_db_path(), "resolve") else str(get_db_path())
    tracks_dir = str(get_tracks_dir().resolve()) if hasattr(get_tracks_dir(), "resolve") else str(get_tracks_dir())

    cmd = list(cmd_base)
    if "--importer" not in cmd and not any("importer" in str(x).lower() for x in cmd):
        cmd.append("--importer")
    cmd.extend(["--db", db_path, "--folder", tracks_dir])

    try:
        if sys.platform == "win32":
            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, creationflags=flags)
        else:
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, start_new_session=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch Smart Importer: {e}")

    return {
        "status": "ok",
        "message": "Tiki Karaoke Smart Importer launched successfully on desktop",
        "cmd": cmd[0]
    }

@app.post("/api/host/launch-cdg-ripper")
async def api_launch_cdg_ripper(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    cb_ripper = get_launch_cdg_ripper_trigger()
    if cb_ripper:
        try:
            cb_ripper()
            return {
                "status": "ok",
                "message": "CD+G Disc Ripper opened successfully on desktop"
            }
        except Exception as e:
            print(f"[API] Error invoking cdg ripper desktop trigger: {e}")

    found = find_importer_executable()
    if not found:
        raise HTTPException(
            status_code=404,
            detail="Tiki Karaoke Ripper/Importer executable not found."
        )

    cmd_base, work_dir = found
    tracks_dir = str(get_tracks_dir().resolve()) if hasattr(get_tracks_dir(), "resolve") else str(get_tracks_dir())

    clean_base = [a for a in cmd_base if a != "--importer"]
    cmd = clean_base + ["--cdg-ripper", "--folder", tracks_dir]

    try:
        if sys.platform == "win32":
            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, creationflags=flags)
        else:
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, start_new_session=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch CD+G Ripper: {e}")

    return {
        "status": "ok",
        "message": "CD+G Optical Disc Ripper launched successfully on desktop",
        "cmd": cmd[0]
    }


@app.post("/api/host/launch-rehearsal-lounge")
async def api_launch_rehearsal_lounge(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")

    found = find_rehearsal_lounge_executable()
    if not found:
        raise HTTPException(
            status_code=404,
            detail="Tiki Rehearsal Lounge executable not found."
        )

    cmd_base, work_dir = found
    cmd = list(cmd_base)

    try:
        if sys.platform == "win32":
            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, creationflags=flags)
        else:
            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, start_new_session=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch Tiki Rehearsal Lounge: {e}")

    return {
        "status": "ok",
        "message": "Tiki Rehearsal Lounge launched successfully on desktop",
        "cmd": cmd[0]
    }


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
        "intermission_vibe_mode": get_setting("intermission_vibe_mode", "sing_along"),
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
    if "intermission_vibe_mode" in payload:
        set_setting("intermission_vibe_mode", str(payload["intermission_vibe_mode"]))
    if "bgm_buffer_seconds" in payload:
        set_setting("bgm_buffer_seconds", str(payload["bgm_buffer_seconds"]))

    await manager.broadcast({
        "type": "filler_settings_updated",
        "settings": {
            "bgm_transition_policy": get_setting("bgm_transition_policy", "fade"),
            "bgm_video_enabled": get_setting("bgm_video_enabled", "true") == "true",
            "bgm_mode": get_setting("bgm_mode", "vibe"),
            "intermission_vibe_mode": get_setting("intermission_vibe_mode", "sing_along"),
            "bgm_buffer_seconds": int(get_setting("bgm_buffer_seconds", "15"))
        }
    })
    return {"status": "ok"}

@app.get("/api/host/strict-mode")
async def api_get_host_strict_mode(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return {
        "status": "ok",
        "strict_byom_only": get_setting("strict_byom_only", "false") == "true"
    }

@app.post("/api/host/strict-mode")
async def api_set_host_strict_mode(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    payload = await request.json()
    enabled = bool(payload.get("enabled", False))
    val = "true" if enabled else "false"
    set_setting("strict_byom_only", val)
    await manager.broadcast({
        "type": "strict_mode_updated",
        "strict_byom_only": enabled
    })
    return {"status": "ok", "strict_byom_only": enabled}

@app.post("/api/host/metadata/refresh")
async def api_refresh_metadata(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    asyncio.create_task(background_metadata_enricher())
    return {"status": "ok", "message": "Metadata enrichment background task started"}

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

    singer_tracks = [q.get("track_id") for q in get_queue() if q.get("singer_id") == singer_id and q.get("status") == "pending"]
    deleted_count = remove_all_songs_by_singer(singer_id)
    for tid in set(singer_tracks):
        maybe_cleanup_temporary_track(tid)
    if checkout:
        checkout_singer(singer_id)

    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })
    return {"status": "ok", "deleted_count": deleted_count, "checked_out": checkout}

@app.post("/api/host/singer/{singer_id}/status")
async def host_set_singer_status(singer_id: int, req: SingerStatusRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    set_singer_status(singer_id, req.status)
    queue = get_queue()
    await manager.broadcast({
        "type": "singer_status_updated",
        "singer_id": singer_id,
        "status": req.status,
        "queue": queue
    })
    await manager.broadcast({
        "type": "singers_updated",
        "singers": get_singers_with_queue()
    })
    return {"status": "ok", "singer_id": singer_id, "status": req.status}

# ------------------------------------------------------------------------------
# Stage Dynamic Marquee Endpoints
# ------------------------------------------------------------------------------

@app.post("/api/host/marquee")
async def api_set_marquee(req: MarqueeMessageRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global active_marquee_message, marquee_expires_at, marquee_mode
    active_marquee_message = req.message.strip() if req.message else None
    if req.duration_minutes and req.duration_minutes > 0:
        marquee_expires_at = time.time() + (req.duration_minutes * 60)
    else:
        marquee_expires_at = None
    if req.mode:
        marquee_mode = req.mode
    if req.speed is not None and req.speed > 0:
        bounded_speed = max(30, min(300, req.speed))
        set_setting("marquee_scroll_speed", str(bounded_speed))

    current_speed = int(get_setting("marquee_scroll_speed", "85"))
    await manager.broadcast({
        "type": "marquee_updated",
        "message": active_marquee_message,
        "mode": marquee_mode,
        "expires_at": marquee_expires_at,
        "speed": current_speed
    })
    return {
        "status": "ok",
        "message": active_marquee_message,
        "mode": marquee_mode,
        "expires_at": marquee_expires_at,
        "speed": current_speed
    }

@app.post("/api/host/marquee/speed")
async def api_set_marquee_speed(req: MarqueeSpeedRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    bounded_speed = max(30, min(300, req.speed))
    set_setting("marquee_scroll_speed", str(bounded_speed))
    await manager.broadcast({
        "type": "marquee_updated",
        "message": active_marquee_message,
        "mode": marquee_mode,
        "expires_at": marquee_expires_at,
        "speed": bounded_speed
    })
    return {"status": "ok", "speed": bounded_speed}

@app.delete("/api/host/marquee")
async def api_clear_marquee(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    global active_marquee_message, marquee_expires_at
    active_marquee_message = None
    marquee_expires_at = None
    current_speed = int(get_setting("marquee_scroll_speed", "85"))
    await manager.broadcast({
        "type": "marquee_updated",
        "message": None,
        "mode": marquee_mode,
        "expires_at": None,
        "speed": current_speed
    })
    return {"status": "ok", "cleared": True, "speed": current_speed}

# ------------------------------------------------------------------------------
# Live Singer Performance Recording Endpoints
# ------------------------------------------------------------------------------

@app.post("/api/recordings/upload")
async def api_upload_recording(
    file: UploadFile = File(...),
    queue_id: Optional[int] = Form(None),
    singer_id: int = Form(...),
    track_id: int = Form(...),
    duration: float = Form(0.0),
    request: Request = None
):
    recordings_dir = get_recordings_dir()
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    track = get_track_by_id(track_id)
    conn = get_db()
    s_row = conn.execute("SELECT id, name, token, avatar FROM singers WHERE id = ?", (singer_id,)).fetchone()
    conn.close()
    singer_name = s_row["name"] if s_row else "Singer"
    singer_token = s_row["token"] if s_row and s_row["token"] else f"usr_{singer_id}"
    singer_avatar = s_row["avatar"] if s_row and "avatar" in s_row.keys() else "🌺"
    track_title = track["title"] if track else "Song"
    track_artist = track["artist"] if track else "Artist"

    # Organize within recordings_dir by singer's persistent cookie / token
    clean_token = re.sub(r'[^\w\-]', '_', singer_token).strip() or f"usr_{singer_id}"
    singer_folder = recordings_dir / clean_token
    singer_folder.mkdir(parents=True, exist_ok=True)

    # Save/update singer_info.json inside singer_folder for human & host reference
    try:
        info_file = singer_folder / "singer_info.json"
        with open(info_file, "w", encoding="utf-8") as f:
            json.dump({
                "singer_id": singer_id,
                "token": singer_token,
                "latest_name": singer_name,
                "avatar": singer_avatar,
                "last_recorded_at": datetime.datetime.now().isoformat()
            }, f, indent=2)
    except Exception:
        pass

    out_dir = singer_folder
    clean_s = re.sub(r'[^\w\-_\. ]', '_', singer_name).strip()
    clean_t = re.sub(r'[^\w\-_\. ]', '_', track_title).strip()
    timestamp = int(time.time())
    base_name = f"{date_str} - {clean_s} - {clean_t}_{timestamp}"

    raw_path = out_dir / f"{base_name}_raw.webm"
    mp3_path = out_dir / f"{base_name}.mp3"

    with open(raw_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    final_path = mp3_path
    ffmpeg_bin = shutil.which("ffmpeg") or str(get_bundled_binary_path("ffmpeg"))
    if ffmpeg_bin and Path(ffmpeg_bin).exists():
        cmd = [
            ffmpeg_bin, "-y", "-i", str(raw_path),
            "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
            "-id3v2_version", "3",
            "-metadata", f"title={track_title}",
            "-metadata", f"artist={singer_name}",
            "-metadata", f"album={BAR_NAME}",
            str(mp3_path)
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if raw_path.exists():
                raw_path.unlink()
        except Exception:
            final_path = raw_path
    else:
        final_path = raw_path

    file_size = final_path.stat().st_size if final_path.exists() else 0
    filename = final_path.name

    rec_id = save_recording(
        queue_id=queue_id,
        singer_id=singer_id,
        track_id=track_id,
        filename=filename,
        file_path=str(final_path),
        file_size=file_size,
        duration_seconds=duration
    )

    # Broadcast notification to singer's smartphone
    await manager.broadcast({
        "type": "recording_ready",
        "recording_id": rec_id,
        "singer_token": singer_token,
        "singer_id": singer_id,
        "song_title": track_title,
        "song_artist": track_artist,
        "download_url": f"/api/recordings/{rec_id}/download",
        "stream_url": f"/api/recordings/{rec_id}/stream"
    })

    return {
        "status": "ok",
        "recording_id": rec_id,
        "filename": filename,
        "download_url": f"/api/recordings/{rec_id}/download"
    }

@app.get("/api/recordings/{recording_id}/stream")
async def api_stream_recording(recording_id: int):
    conn = get_db()
    row = conn.execute("SELECT file_path, filename FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    conn.close()
    if not row or not Path(row["file_path"]).exists():
        raise HTTPException(status_code=404, detail="Recording file not found")
    media_type = "audio/mpeg" if row["filename"].endswith(".mp3") else "audio/webm"
    return FileResponse(row["file_path"], media_type=media_type)

@app.get("/api/recordings/{recording_id}/download")
async def api_download_recording(recording_id: int):
    conn = get_db()
    row = conn.execute("SELECT file_path, filename FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    conn.close()
    if not row or not Path(row["file_path"]).exists():
        raise HTTPException(status_code=404, detail="Recording file not found")
    return FileResponse(row["file_path"], filename=row["filename"], media_type="application/octet-stream")

@app.get("/api/singer/{token}/recordings")
async def api_get_singer_recordings(token: str):
    singer = get_singer_by_token(token)
    if not singer:
        return []
    return get_recordings_for_singer(singer["id"])

@app.get("/api/host/recordings")
async def api_get_host_recordings(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_all_recordings()

@app.delete("/api/host/recordings/{recording_id}")
async def api_delete_recording(recording_id: int, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    file_path = delete_recording(recording_id)
    if file_path:
        p = Path(file_path)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
    return {"status": "ok", "deleted": recording_id}

@app.get("/api/host/storage/recordings")
async def api_get_host_recordings_storage(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    rec_dir = get_recordings_dir()
    def_dir = get_default_recordings_dir()
    total_bytes = 0
    total_count = 0
    singer_dirs = set()
    if rec_dir.exists():
        try:
            for f in rec_dir.glob("**/*"):
                if f.is_file() and f.suffix.lower() in (".mp3", ".webm", ".wav", ".m4a"):
                    total_bytes += f.stat().st_size
                    total_count += 1
                    singer_dirs.add(f.parent.name)
        except Exception:
            pass
    return {
        "status": "ok",
        "recordings_dir": str(rec_dir),
        "default_recordings_dir": str(def_dir),
        "is_custom": (str(rec_dir) != str(def_dir)),
        "count": total_count,
        "bytes": total_bytes,
        "mb": round(total_bytes / (1024**2), 1),
        "singers_count": len(singer_dirs)
    }

@app.post("/api/host/storage/recordings")
async def api_update_host_recordings_storage(req: HostUpdateRecordingsPathRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    target_path_str = req.recordings_dir.strip()
    if target_path_str:
        target_path = Path(target_path_str).expanduser().resolve()
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            test_file = target_path / ".tiki_write_test"
            test_file.touch()
            test_file.unlink()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot write to folder: {e}")
        save_config({"recordings_dir": str(target_path)})
        set_setting("recordings_dir", str(target_path))
    else:
        # Revert to default
        save_config({"recordings_dir": ""})
        set_setting("recordings_dir", "")

    return {
        "status": "ok",
        "recordings_dir": str(get_recordings_dir()),
        "default_recordings_dir": str(get_default_recordings_dir())
    }

@app.post("/api/host/storage/recordings/open-folder")
async def api_open_recordings_folder(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    rec_dir = get_recordings_dir()
    rec_dir.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(rec_dir))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(rec_dir)], check=False)
        else:
            subprocess.run(["xdg-open", str(rec_dir)], check=False)
        return {"status": "ok", "message": f"Opened {rec_dir}"}
    except Exception as e:
        return {"status": "error", "detail": str(e), "path": str(rec_dir)}


@app.post("/api/host/singers/purge-all")
async def host_purge_all_singers(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    all_queued_tracks = [q.get("track_id") for q in get_queue()]
    res = purge_all_singers(include_history=True)
    for tid in set(all_queued_tracks):
        maybe_cleanup_temporary_track(tid)
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

class HotspotConfigPayload(BaseModel):
    ssid: str = ""
    password: str = ""
    enabled: bool = True

@app.get("/api/host/network")
async def host_get_network_status(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    import network_helper
    from config import PORT
    effective_port = int(os.getenv("TIKI_PORT", str(PORT)))
    ip = network_helper.get_best_local_ip()
    is_hotspot = network_helper.is_hotspot_ip(ip)
    is_net = network_helper.is_internet_available()
    ssid = network_helper.get_hotspot_ssid()
    password = network_helper.get_hotspot_password()
    show_wifi_card = network_helper.is_hotspot_enabled()
    base_url = get_base_url()
    join_url = f"{base_url}/join"
    cfg = load_config()
    pin = str(cfg.get("host_pin", HOST_PIN))
    return {
        "status": "ok",
        "local_ip": ip,
        "port": effective_port,
        "base_url": base_url,
        "join_url": join_url,
        "host_pin": pin,
        "is_internet_connected": is_net,
        "is_hotspot": is_hotspot,
        "hotspot_enabled": get_setting("hotspot_enabled", "false") == "true",
        "hotspot_ssid": ssid,
        "hotspot_password": password,
        "show_wifi_card": show_wifi_card
    }

@app.post("/api/host/network/hotspot-config")
async def host_update_hotspot_config(payload: HotspotConfigPayload, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    import network_helper
    from config import PORT
    effective_port = int(os.getenv("TIKI_PORT", str(PORT)))
    network_helper.save_hotspot_config(payload.ssid, payload.password, payload.enabled)
    ip = network_helper.get_best_local_ip()
    is_hotspot = network_helper.is_hotspot_ip(ip)
    is_net = network_helper.is_internet_available()
    show_wifi_card = network_helper.is_hotspot_enabled()
    base_url = get_base_url()
    join_url = f"{base_url}/join"
    
    await manager.broadcast({
        "type": "network_updated",
        "local_ip": ip,
        "port": effective_port,
        "base_url": base_url,
        "join_url": join_url,
        "is_internet_connected": is_net,
        "is_hotspot": is_hotspot,
        "hotspot_enabled": payload.enabled,
        "hotspot_ssid": payload.ssid.strip(),
        "hotspot_password": payload.password.strip(),
        "show_wifi_card": show_wifi_card
    })
    
    return {
        "status": "ok",
        "local_ip": ip,
        "port": effective_port,
        "base_url": base_url,
        "join_url": join_url,
        "is_internet_connected": is_net,
        "is_hotspot": is_hotspot,
        "hotspot_enabled": payload.enabled,
        "hotspot_ssid": payload.ssid.strip(),
        "hotspot_password": payload.password.strip(),
        "show_wifi_card": show_wifi_card
    }


from services.lifecycle import (
    register_shutdown_trigger, register_focus_hub_trigger,
    register_launch_importer_trigger, register_launch_cdg_ripper_trigger,
    register_tunnel_reload_trigger, get_tunnel_reload_trigger,
    get_shutdown_trigger, get_focus_hub_trigger,
    get_launch_importer_trigger, get_launch_cdg_ripper_trigger
)

@app.post("/api/host/focus_hub")
async def host_focus_hub_action(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    cb = get_focus_hub_trigger()
    if cb:
        cb()
    return {"status": "ok"}

@app.post("/api/host/shutdown")
async def host_shutdown_action(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    
    await manager.broadcast({
        "type": "server_shutting_down",
        "message": "Host initiated server shutdown. Aloha! 🌺"
    })
    
    def _delayed_exit():
        time.sleep(0.6)
        cb = get_shutdown_trigger()
        if cb:
            cb()
        else:
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                sys.exit(0)

    threading.Thread(target=_delayed_exit, daemon=True).start()
    return {"status": "ok", "message": "Tiki Karaoke is shutting down cleanly..."}

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
