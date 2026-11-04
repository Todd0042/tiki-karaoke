"""
routes/host_controls.py - Host DJ transport controls, playback management, and system triggers.

Provides all DJ dashboard operations:
- Playback controls: play, pause, skip, stop, add time, refresh stage
- Live queue completion & automatic YouTube playlist history sync
- State synchronization endpoint (/api/state) with real-time countdowns & marquee
- Intermission filler music video search, staging, and playback
- Local media batch import (video, MP3+CDG zip, uncompressed CDG pairs)
- System diagnostics: ffmpeg health, yt-dlp version check & in-place update
- Host microphone settings & Web Audio / PipeWire / native loopback monitoring
- Display routing & multi-screen audio assignment
- Hotspot network configuration & clean shutdown triggers
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import network_helper
from audio_router import audio_router
from config import (
    DEFAULT_INTERMISSION_SECONDS,
    HOST_PIN,
    PORT,
    REMOTE_MODE,
    STATIC_DIR,
    get_bundled_binary_path,
    get_data_dir,
    get_default_library_dir,
    get_library_dir,
    get_user_data_root,
    load_config,
)
from database import (
    add_filler_track,
    checkout_singer,
    delete_filler_track,
    get_all_settings,
    get_current_playing,
    get_filler_track_by_id,
    get_filler_track_by_video_id,
    get_filler_tracks,
    get_next_vibe_filler_track,
    get_party_wrapped_stats,
    get_queue,
    get_setting,
    get_singers_with_queue,
    get_track_by_id,
    import_local_filler_track,
    import_local_track,
    purge_all_singers,
    record_filler_play,
    remove_all_songs_by_singer,
    search_local_tracks,
    set_queue_status,
    set_setting,
    set_singer_status,
    update_filler_track_status,
)
from downloader import enqueue_filler_download
from metadata import fetch_track_metadata
from playlist_sync import is_authenticated as is_yt_authenticated, sync_played_song_to_youtube
from routes.queue import SingerStatusRequest
from search import query_youtube_music_videos
from services.auth import (
    HOST_TOKEN,
    is_host_request,
    is_local_request,
    is_stage_request,
)
from services.lifecycle import get_focus_hub_trigger, get_shutdown_trigger
from services.network import get_base_url
import services.playback as playback
from services.temporary_cache import maybe_cleanup_temporary_track
from services.websocket_manager import manager

router = APIRouter(tags=["host_controls"])


# ------------------------------------------------------------------------------
# Request Models
# ------------------------------------------------------------------------------

class MarqueeMessageRequest(BaseModel):
    message: str
    duration_minutes: Optional[int] = 0
    mode: Optional[str] = "auto"
    speed: Optional[int] = None


class MarqueeSpeedRequest(BaseModel):
    speed: int


class DisplayAudioAction(BaseModel):
    action: str  # "mute", "unmute", "set_master"


class AVSyncPayload(BaseModel):
    offset_ms: int


class HotspotConfigPayload(BaseModel):
    ssid: str = ""
    password: str = ""
    enabled: bool = True


class ImportLocalMediaRequest(BaseModel):
    folder_path: str
    recursive: bool = True
    is_sing_along: bool = False
    target_destination: str = "karaoke"  # "karaoke", "sing_along", "filler"


# ------------------------------------------------------------------------------
# Playback Endpoints
# ------------------------------------------------------------------------------

@router.post("/api/queue/{queue_id}/play")
async def api_play_queue_item(queue_id: int, request: Request):
    if not is_host_request(request) and not is_local_request(request) and not is_stage_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    playback.set_intermission_end(None)
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


@router.post("/api/queue/{queue_id}/complete")
async def api_complete_queue_item(queue_id: int, request: Request):
    if not is_host_request(request) and not is_local_request(request) and not is_stage_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    queue_list = get_queue()
    item = next((q for q in queue_list if q["queue_id"] == queue_id), None)

    set_queue_status(queue_id, "completed")
    if item:
        maybe_cleanup_temporary_track(item.get("track_id"))

    # Sync to YouTube playlist in background if authenticated
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
        playback.set_intermission_end(time.time() + dur)
        await manager.broadcast({
            "type": "start_intermission",
            "next_track": next_up,
            "duration": dur
        })
    else:
        playback.set_intermission_end(None)
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
        await playback.trigger_filler_sequence(last_artist=last_artist, last_genre=last_genre, last_year=last_year)

    return {"status": "completed"}


@router.get("/api/state")
async def api_get_state():
    current = get_current_playing()
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    active_pending = [q for q in pending if q.get("singer_status", "active") == "active"]
    next_up = active_pending[0] if active_pending else None
    stage_displays = manager.get_stage_displays()

    cur_intermission_end = playback.get_intermission_end()
    intermission_remaining = 0
    if not current and next_up and cur_intermission_end:
        intermission_remaining = max(0, int(cur_intermission_end - time.time()))
    elif not current and next_up:
        intermission_remaining = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))

    # Prefer downloaded BGM videos when stage is idle and active queue is empty
    if not current and not next_up:
        if get_setting("bgm_video_enabled", "true") == "true" and not playback.filler_active:
            pick = get_next_vibe_filler_track()
            if pick:
                playback.filler_active = True
                playback.filler_transition_state = "playing"
                playback.filler_current_track = pick
                playback.filler_buffer_end = None
                record_filler_play(pick["id"])

    # Check marquee message expiry
    if playback.marquee_expires_at and time.time() > playback.marquee_expires_at:
        playback.active_marquee_message = None
        playback.marquee_expires_at = None

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
            "message": playback.active_marquee_message,
            "expires_at": playback.marquee_expires_at,
            "mode": playback.marquee_mode,
            "queue_ticker": queue_ticker_str,
            "current_dedication": current.get("dedication", "") if current else "",
            "speed": int(get_setting("marquee_scroll_speed", "85"))
        },
        "intermission_seconds": intermission_remaining,
        "has_stage_display": len(stage_displays) > 0,
        "waiting_for_display": False,
        "filler": {
            "active": playback.filler_active,
            "state": playback.filler_transition_state,
            "current_track": playback.filler_current_track,
            "buffer_remaining": max(0, int(playback.filler_buffer_end - time.time())) if playback.filler_buffer_end else 0,
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


@router.get("/api/host/health")
async def api_host_health(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    effective_port = int(os.getenv("TIKI_PORT", str(PORT)))
    return {
        "status": "ok",
        "ffmpeg_installed": bool(shutil.which("ffmpeg") or get_bundled_binary_path("ffmpeg").exists()),
        "ffprobe_installed": bool(shutil.which("ffprobe") or get_bundled_binary_path("ffprobe").exists()),
        "port": effective_port,
        "python_version": sys.version.split()[0],
        "platform": sys.platform
    }


@router.get("/api/auth/role")
async def api_get_role(request: Request):
    return {"is_host": is_host_request(request)}


@router.get("/api/settings")
async def api_get_settings():
    return get_all_settings()


@router.post("/api/settings")
async def api_update_settings(settings: Dict[str, str], request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    for k, v in settings.items():
        set_setting(k, str(v))
    return {"status": "ok"}


@router.get("/api/host/mic-state")
async def api_get_host_mic_state():
    st = audio_router.get_state()
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


@router.post("/api/host/mic-monitoring-mode")
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


@router.get("/api/bgm-tracks")
async def api_get_bgm_tracks():
    bgm_dir = STATIC_DIR / "bgm"
    if not bgm_dir.exists():
        return {"tracks": []}
    tracks = sorted([f"/static/bgm/{f.name}" for f in bgm_dir.glob("*.mp3")])
    return {"tracks": tracks}


@router.post("/api/host/skip")
async def host_skip_action(request: Request):
    if not is_host_request(request) and not is_local_request(request) and not is_stage_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
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
            playback.set_intermission_end(time.time() + dur)
            await manager.broadcast({
                "type": "start_intermission",
                "next_track": pending[0],
                "duration": dur
            })
        else:
            playback.set_intermission_end(None)
            await manager.broadcast({"type": "queue_empty"})
            if get_setting("bgm_video_enabled", "true") == "true":
                last_artist = current.get("artist", "")
                await playback.trigger_filler_sequence(last_artist=last_artist)
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
            playback.set_intermission_end(time.time() + dur)
            await manager.broadcast({
                "type": "start_intermission",
                "next_track": pending[0],
                "duration": dur
            })
        else:
            playback.set_intermission_end(None)
            await manager.broadcast({"type": "queue_empty"})
            if get_setting("bgm_video_enabled", "true") == "true":
                await playback.trigger_filler_sequence()
        await manager.broadcast({"type": "queue_updated", "queue": queue})
        return {"status": "ok"}

    else:
        bgm_video_on = get_setting("bgm_video_enabled", "true") == "true"
        current_id = playback.filler_current_track.get("id") if playback.filler_current_track else None
        last_artist = playback.filler_current_track.get("artist", "") if playback.filler_current_track else ""
        last_genre = playback.filler_current_track.get("genre", "") if playback.filler_current_track else ""
        last_year = playback.filler_current_track.get("release_year", 0) if playback.filler_current_track else 0
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
            playback.filler_current_track = next_filler
            playback.filler_active = True
            playback.filler_transition_state = "playing"
            playback.filler_buffer_end = None
            record_filler_play(next_filler["id"], is_sing_along=bool(next_filler.get("is_sing_along")))
            await manager.broadcast({
                "type": "play_filler_track",
                "track": next_filler
            })
            return {"status": "ok", "skipped_to_filler": f"{next_filler.get('artist', '')} - {next_filler.get('title', '')}"}
        else:
            await manager.broadcast({"type": "skip_bgm"})
            return {"status": "ok", "skipped_to_bgm": True}


@router.post("/api/host/stop")
async def host_stop_action(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    playback.set_intermission_end(None)

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
        await playback.trigger_filler_sequence(is_paused=True)

    return {"status": "ok"}


# ------------------------------------------------------------------------------
# BGM Filler Endpoints
# ------------------------------------------------------------------------------

@router.get("/api/host/search-filler")
async def api_search_filler(request: Request, q: str = ""):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return query_youtube_music_videos(q)


@router.get("/api/host/filler-tracks")
async def api_get_filler_tracks(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_filler_tracks()


@router.post("/api/host/filler-tracks/stage")
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


@router.post("/api/host/filler-tracks/download-batch")
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


@router.delete("/api/host/filler-tracks/{filler_id}")
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


@router.post("/api/host/filler-tracks/{filler_id}/play")
async def api_force_play_filler(filler_id: int, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    track = get_filler_track_by_id(filler_id)
    if not track:
        raise HTTPException(status_code=404, detail="Filler track not found")

    playback.filler_active = True
    playback.filler_transition_state = "playing"
    playback.filler_current_track = track
    playback.filler_buffer_end = None
    record_filler_play(filler_id)

    await manager.broadcast({
        "type": "play_filler_track",
        "track": track
    })
    return {"status": "ok", "playing": track}


@router.post("/api/host/filler-tracks/finished")
async def api_filler_finished(request: Request):
    await playback.on_filler_track_ended()
    return {"status": "ok"}


@router.get("/api/host/default-paths")
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


@router.post("/api/host/import-local-library")
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

    for p in all_files:
        if p.suffix.lower() == ".zip":
            extracted = playback.extract_cdg_zip(p)
            if extracted:
                audio_p, cdg_p = extracted
                cdg_items.append((p.name, audio_p, cdg_p))

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

    for display_name, audio_p, cdg_p in cdg_items:
        artist, title = playback.parse_karaoke_filename(display_name)
        dur = playback.get_local_media_duration(audio_p)
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

    for f in all_files:
        ext = f.suffix.lower()
        res_str = str(f.resolve())
        if ext in VIDEO_EXTS or (ext in AUDIO_EXTS and res_str not in processed_audio_paths and is_filler):
            artist, title = playback.parse_karaoke_filename(f.name)
            dur = playback.get_local_media_duration(res_str)
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


@router.post("/api/host/filler/import-from-song/{track_id}")
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


@router.get("/api/host/filler-settings")
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


@router.post("/api/host/filler-settings")
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


@router.get("/api/host/strict-mode")
async def api_get_host_strict_mode(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return {
        "status": "ok",
        "strict_byom_only": get_setting("strict_byom_only", "false") == "true"
    }


@router.post("/api/host/strict-mode")
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


@router.post("/api/host/metadata/refresh")
async def api_refresh_metadata(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    asyncio.create_task(playback.background_metadata_enricher())
    return {"status": "ok", "message": "Metadata enrichment background task started"}


@router.post("/api/host/toggle-pause")
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


@router.post("/api/host/start-now")
async def host_start_now_action(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    if pending:
        next_track = pending[0]
        await api_play_queue_item(next_track["queue_id"], request)
    return {"status": "ok"}


@router.post("/api/host/add-time")
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


@router.post("/api/host/stage/refresh")
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


@router.get("/api/host/singers")
async def host_get_singers(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_singers_with_queue()


@router.post("/api/host/singer/{singer_id}/remove-all")
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


@router.post("/api/host/singer/{singer_id}/status")
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


@router.post("/api/host/marquee")
async def api_set_marquee(req: MarqueeMessageRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    playback.active_marquee_message = req.message.strip() if req.message else None
    if req.duration_minutes and req.duration_minutes > 0:
        playback.marquee_expires_at = time.time() + (req.duration_minutes * 60)
    else:
        playback.marquee_expires_at = None
    if req.mode:
        playback.marquee_mode = req.mode
    if req.speed is not None and req.speed > 0:
        bounded_speed = max(30, min(300, req.speed))
        set_setting("marquee_scroll_speed", str(bounded_speed))

    current_speed = int(get_setting("marquee_scroll_speed", "85"))
    await manager.broadcast({
        "type": "marquee_updated",
        "message": playback.active_marquee_message,
        "mode": playback.marquee_mode,
        "expires_at": playback.marquee_expires_at,
        "speed": current_speed
    })
    return {
        "status": "ok",
        "message": playback.active_marquee_message,
        "mode": playback.marquee_mode,
        "expires_at": playback.marquee_expires_at,
        "speed": current_speed
    }


@router.post("/api/host/marquee/speed")
async def api_set_marquee_speed(req: MarqueeSpeedRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    bounded_speed = max(30, min(300, req.speed))
    set_setting("marquee_scroll_speed", str(bounded_speed))
    await manager.broadcast({
        "type": "marquee_updated",
        "message": playback.active_marquee_message,
        "mode": playback.marquee_mode,
        "expires_at": playback.marquee_expires_at,
        "speed": bounded_speed
    })
    return {"status": "ok", "speed": bounded_speed}


@router.delete("/api/host/marquee")
async def api_clear_marquee(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    playback.active_marquee_message = None
    playback.marquee_expires_at = None
    current_speed = int(get_setting("marquee_scroll_speed", "85"))
    await manager.broadcast({
        "type": "marquee_updated",
        "message": None,
        "mode": playback.marquee_mode,
        "expires_at": None,
        "speed": current_speed
    })
    return {"status": "ok", "cleared": True, "speed": current_speed}


@router.post("/api/host/singers/purge-all")
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


@router.get("/api/host/displays")
async def host_get_displays(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return manager.get_stage_displays()


@router.post("/api/host/display/{display_id}/audio")
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


@router.get("/api/host/system/ytdlp-status")
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


@router.post("/api/host/system/update-ytdlp")
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


@router.get("/api/host/av-sync")
async def host_get_av_sync(request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    raw = get_setting("av_sync_offset_ms", "0")
    try:
        offset_ms = int(raw)
    except Exception:
        offset_ms = 0
    return {"status": "ok", "offset_ms": offset_ms}


@router.post("/api/host/av-sync")
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


@router.get("/api/host/network")
async def host_get_network_status(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
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


@router.post("/api/host/network/hotspot-config")
async def host_update_hotspot_config(payload: HotspotConfigPayload, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
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


@router.post("/api/host/focus_hub")
async def host_focus_hub_action(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    cb = get_focus_hub_trigger()
    if cb:
        cb()
    return {"status": "ok"}


@router.post("/api/host/shutdown")
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


@router.post("/api/stage/notify-on-deck")
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


@router.get("/api/surprise")
async def api_surprise():
    tracks = search_local_tracks("", limit=50)
    if not tracks:
        return None
    import random
    return random.choice(tracks)


@router.get("/api/wrapped")
async def api_wrapped():
    return get_party_wrapped_stats()
