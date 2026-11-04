"""
Singer & Queue Management Router for Tiki Karaoke.
Handles singer registration, profile lookup, avatar selection, songbook history,
song requests/queueing, duet partnerships, abuse-prevention rate limits,
fair rotation ordering, and pending download staging.
"""

import os
import time
import secrets
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from database import (
    get_or_create_singer, get_taken_avatars_for_name,
    get_singer_by_token, get_singer_history, get_singer_songbook,
    set_singer_status_by_token, get_queue, check_song_queue_duplicate,
    add_duet_partner, remove_duet_partner, get_pending_queue_count_for_singer,
    get_track_by_id, get_track_by_video_id, save_track, set_track_sing_along,
    add_pending_download, enqueue_song, get_current_playing,
    get_pending_downloads, get_pending_download_by_id,
    increment_pending_download_retry, remove_pending_download,
    remove_from_queue, remove_all_songs_by_singer, checkout_singer,
    get_setting
)
from downloader import enqueue_download
from services.auth import is_host_request, is_local_request
from services.websocket_manager import manager
from services.temporary_cache import maybe_cleanup_temporary_track

router = APIRouter()

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
    is_sing_along: bool = False
    dedication: str = ""
    stage_download: bool = False

class SingerStatusRequest(BaseModel):
    status: str  # "active", "on_break", "checked_out"

class JoinDuetRequest(BaseModel):
    singer_token: str
    partner_name: Optional[str] = None

class LeaveDuetRequest(BaseModel):
    singer_token: str
    singer_name: Optional[str] = None

# Rate-limiting state
last_queue_time_by_singer: Dict[str, float] = {}
last_queue_time_by_ip: Dict[str, float] = {}


# Singer Endpoints
@router.post("/api/singer/register")
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


@router.get("/api/singer/available-avatars")
async def api_available_avatars(request: Request, name: str = "", token: str = ""):
    client_ip = request.client.host if request.client else ""
    is_host = is_host_request(request) or is_local_request(request)
    taken = get_taken_avatars_for_name(name, exclude_token=token, client_ip=client_ip, is_host=is_host)
    return {
        "name": name,
        "taken_avatars": taken
    }


@router.get("/api/singer/{token}/history")
async def api_singer_history(token: str):
    singer = get_singer_by_token(token)
    if not singer:
        return []
    return get_singer_history(singer["id"])


@router.get("/api/singer/{token}/songbook")
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


@router.get("/api/singer/{token}/profile")
async def api_singer_profile(token: str):
    singer = get_singer_by_token(token)
    if not singer:
        raise HTTPException(status_code=404, detail="Singer profile not found.")
    return {
        "token": singer["token"],
        "name": singer["name"],
        "avatar": singer["avatar"],
        "status": singer.get("status", "active")
    }


@router.post("/api/singer/{token}/status")
async def api_singer_set_status(token: str, req: SingerStatusRequest):
    updated = set_singer_status_by_token(token, req.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Singer profile not found")
    await manager.broadcast({
        "type": "singer_status_updated",
        "singer_id": updated["id"],
        "status": updated.get("status", "active"),
        "queue": get_queue()
    })
    return {"status": "ok", "singer": updated}


@router.post("/api/singer/leave")
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
    singer_tracks = [q.get("track_id") for q in get_queue() if q.get("singer_id") == singer["id"] and q.get("status") == "pending"]
    remove_all_songs_by_singer(singer["id"])
    for tid in set(singer_tracks):
        maybe_cleanup_temporary_track(tid)
    checkout_singer(singer["id"])
    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })
    return {"status": "ok", "checked_out": True, "message": f"{singer['name']} checked out cleanly"}


# Queue Endpoints
@router.get("/api/queue")
async def api_get_queue():
    return get_queue()


@router.get("/api/queue/check-duplicate")
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


@router.post("/api/queue/{queue_id}/join-duet")
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


@router.post("/api/queue/{queue_id}/leave-duet")
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


@router.post("/api/queue")
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

    # Curated Local Library / Offline Mode: Reject non-imported tracks
    if get_setting("strict_byom_only", "false") == "true":
        track_obj = get_track_by_id(req.track_id) if req.track_id else (get_track_by_video_id(req.video_id) if req.video_id else None)
        is_imported = False
        if track_obj:
            is_imported = (track_obj.get("media_source") == "imported") or str(track_obj.get("video_id", "")).startswith("local_")
        elif req.video_id and req.video_id.startswith("local_"):
            is_imported = True

        if not is_imported:
            raise HTTPException(
                status_code=403,
                detail="🔒 Curated Local Library Mode Active: Online downloads are disabled by the Host DJ. Only imported songs in the local crate can be queued."
            )

    # Ensure track is in DB
    track_id = req.track_id
    if not track_id:
        track_id = save_track(
            video_id=req.video_id,
            title=req.title,
            artist=req.artist,
            original_title=f"{req.artist} - {req.title}",
            is_sing_along=req.is_sing_along
        )
    elif req.is_sing_along:
        set_track_sing_along(track_id, True)

    if not is_host:
        now = time.time()
        last_queue_time_by_singer[singer_key] = now

    track = get_track_by_id(track_id)

    is_testing = bool(os.environ.get("TIKI_TESTING"))
    # In live production, all un-downloaded YouTube tracks stage in pending_downloads first.
    # In automated test suites, fake IDs enter active queue directly unless testing staging explicitly.
    should_stage = (not track["is_downloaded"]) and (not is_testing or req.stage_download or req.video_id.startswith("online_") or req.video_id.startswith("pending_"))

    # If track is NOT yet downloaded to disk (and not local imported), stage it in pending_downloads!
    if track and should_stage:
        pending_id = add_pending_download(
            track_id=track_id,
            singer_id=singer["id"],
            duet_partner=req.duet_partner,
            pitch_shift=req.pitch_shift,
            dedication=getattr(req, "dedication", ""),
            quality_profile="default"
        )
        enqueue_download(
            track_id=track_id,
            singer_name=singer["name"],
            title=req.title,
            artist=req.artist,
            pending_id=pending_id,
            quality_profile="default"
        )
        await manager.broadcast({
            "type": "pending_downloads_updated",
            "pending_downloads": get_pending_downloads()
        })
        return {
            "status": "ok",
            "pending_id": pending_id,
            "is_pending_download": True,
            "message": f"Downloading {req.title}. It will automatically enter the stage queue once ready!"
        }

    # Track is already downloaded / local: Enqueue directly into active stage queue
    queue_id = enqueue_song(
        track_id=track_id,
        singer_id=singer["id"],
        duet_partner=req.duet_partner,
        pitch_shift=req.pitch_shift,
        dedication=getattr(req, "dedication", "")
    )

    current_playing = get_current_playing()
    queue = get_queue()
    await manager.broadcast({
        "type": "queue_updated",
        "queue": queue
    })

    try:
        from app import trigger_intermission_if_idle
        await trigger_intermission_if_idle()
    except Exception:
        pass

    return {"status": "ok", "queue_id": queue_id}


@router.get("/api/downloads/pending")
async def api_get_pending_downloads():
    return {"status": "ok", "pending_downloads": get_pending_downloads()}


@router.post("/api/download/retry/{pending_id}")
async def api_retry_download(pending_id: int, request: Request, quality: str = "default"):
    pending = get_pending_download_by_id(pending_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Pending download not found")

    increment_pending_download_retry(pending_id, quality_profile=quality)
    enqueue_download(
        track_id=pending["track_id"],
        singer_name=pending["singer_name"],
        title=pending["title"],
        artist=pending["artist"],
        pending_id=pending_id,
        quality_profile=quality
    )
    await manager.broadcast({
        "type": "pending_downloads_updated",
        "pending_downloads": get_pending_downloads()
    })
    return {"status": "ok", "message": f"Retrying download with quality: {quality}"}


@router.delete("/api/download/{pending_id}")
async def api_cancel_pending_download(pending_id: int, request: Request):
    pending = get_pending_download_by_id(pending_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Pending download not found")

    singer_token = request.cookies.get("singer_token") or request.headers.get("X-Singer-Token", "")
    is_host = is_host_request(request)
    singer = get_singer_by_token(singer_token) if singer_token else None

    if not is_host:
        if not singer or singer["id"] != pending["singer_id"]:
            raise HTTPException(status_code=403, detail="You can only remove your own requested songs.")

    removed = remove_pending_download(pending_id)
    await manager.broadcast({
        "type": "pending_downloads_updated",
        "pending_downloads": get_pending_downloads()
    })
    return {"status": "ok", "removed": removed}


@router.delete("/api/queue/{queue_id}")
async def api_remove_queue(queue_id: int, request: Request, token: str = ""):
    is_host = is_host_request(request) or is_local_request(request)
    if not is_host:
        singer = get_singer_by_token(token)
        if not singer:
            raise HTTPException(status_code=401, detail="Authentication required to remove song")
        singer_id = singer["id"]
    else:
        singer_id = None
    queue_before = get_queue()
    target_item = next((q for q in queue_before if q["queue_id"] == queue_id), None)
    deleted = remove_from_queue(queue_id, singer_id=singer_id)
    if deleted:
        if target_item:
            maybe_cleanup_temporary_track(target_item.get("track_id"))
        await manager.broadcast({
            "type": "queue_updated",
            "queue": get_queue()
        })
    return {"deleted": deleted}
