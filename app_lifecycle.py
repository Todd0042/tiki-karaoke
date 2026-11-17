import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI

import network_helper
from audio_router import audio_router
from config import HOST_PIN, HOST_TOKEN, PORT
from database import (
    clear_queue_on_startup,
    get_current_playing,
    get_pending_downloads,
    get_queue,
    get_setting,
    init_db,
    set_queue_status,
)
from downloader import downloader_worker, enqueue_download, filler_downloader_worker
from services.auth import is_room_code_required
from services.library_cache import warm_library_cache
from services.network import get_base_url, get_join_url
from services.playback import background_metadata_enricher
from services.websocket_manager import manager

logger = logging.getLogger("tiki.lifecycle")

main_event_loop: Optional[asyncio.AbstractEventLoop] = None
intermission_state: Dict[str, Any] = {"current_intermission_end": None}


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
    req_room = is_room_code_required()
    join_url = get_join_url() if not req_room else f"{base_url}/join"
    payload = {
        "type": "network_updated",
        "local_ip": ip,
        "port": effective_port,
        "base_url": base_url,
        "join_url": join_url,
        "require_room_code": req_room,
        "is_internet_connected": is_net,
        "is_hotspot": is_hotspot,
        "hotspot_enabled": show_wifi_card,
        "hotspot_ssid": get_setting("hotspot_ssid", ""),
        "hotspot_password": get_setting("hotspot_password", ""),
        "show_wifi_card": show_wifi_card,
    }
    asyncio.run_coroutine_threadsafe(manager.broadcast(payload), main_event_loop)


async def warm_library_cache_with_progress():
    """Warm library cache on startup and broadcast progress status to connected clients."""
    try:
        await manager.broadcast({"type": "library_warm_progress", "status": "warming"})
        await asyncio.to_thread(warm_library_cache)
        await manager.broadcast({"type": "library_warm_progress", "status": "ready"})
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.warning(f"[Startup] Library cache warming failed: {e}")


async def intermission_watchdog_loop():
    """Background watchdog ensuring tracks auto-start when intermission timer ends."""
    while True:
        try:
            await asyncio.sleep(1.0)
            end_ts = intermission_state.get("current_intermission_end")
            if end_ts and time.time() >= end_ts:
                current = get_current_playing()
                if not current:
                    queue = get_queue()
                    pending = [q for q in queue if q["status"] == "pending"]
                    if pending:
                        next_up = pending[0]
                        intermission_state["current_intermission_end"] = None
                        logger.info(
                            f"[Watchdog] Intermission completed. Auto-starting track {next_up['queue_id']} ({next_up['title']})"
                        )
                        set_queue_status(next_up["queue_id"], "playing")
                        playing_track = get_current_playing()
                        await manager.broadcast({"type": "play_track", "track": playing_track})
                        await manager.broadcast({"type": "queue_updated", "queue": get_queue()})
                    else:
                        intermission_state["current_intermission_end"] = None
                else:
                    intermission_state["current_intermission_end"] = None
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"[Watchdog] Error: {e}")


def _resume_pending_downloads():
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
                    quality_profile=p.get("quality_profile", "default"),
                )
        if pending_list:
            logger.info(f"[Startup] Resumed {len(pending_list)} staged downloads from database.")
    except Exception as e:
        logger.warning(f"[Startup] Error resuming pending downloads: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_event_loop
    main_event_loop = asyncio.get_running_loop()
    init_db()
    clear_queue_on_startup()
    saved_mode = get_setting("mic_monitoring_mode", "native")
    audio_router.set_monitoring_mode(saved_mode)

    worker_task = asyncio.create_task(downloader_worker())
    filler_worker_task = asyncio.create_task(filler_downloader_worker())
    watchdog_task = asyncio.create_task(intermission_watchdog_loop())
    enricher_task = asyncio.create_task(background_metadata_enricher())
    warm_task = asyncio.create_task(warm_library_cache_with_progress())

    _resume_pending_downloads()

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
    warm_task.cancel()
    audio_router.stop_loopback()
