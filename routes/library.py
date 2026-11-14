"""
Library & Storage Management Router for Tiki Karaoke.
Handles song catalog searching, library pagination, disk storage monitoring,
track pruning, metadata editing, track type conversion, and host track downloads.
"""

import time
import asyncio
import sys
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from config import get_storage_status
from database import (
    get_browsable_tracks, get_unique_downloaded_artists,
    get_cleanup_candidates, prune_local_track, get_queue,
    get_track_by_id, edit_track_metadata, convert_track_type
)
from search import tiered_search
from downloader import enqueue_download
from services.auth import get_client_ip, is_host_request, is_local_request, require_room_access
from services.websocket_manager import manager
from services.library_cache import get_cached_unique_artists, invalidate_library_cache

router = APIRouter()

# Request Models
class PruneTracksRequest(BaseModel):
    track_ids: List[int] = []
    filler_ids: List[int] = []

class EditTrackMetadataRequest(BaseModel):
    title: str
    artist: str
    is_filler: bool = False

class ConvertTrackTypeRequest(BaseModel):
    target_type: str  # "karaoke", "sing_along", "music_video"
    source_type: str = "auto"
    is_filler: bool = False

# Search rate-limiting state
search_requests_by_ip: Dict[str, List[float]] = {}


@router.get("/api/search")
async def api_search(request: Request, q: str = "", force_online: bool = False, sing_along: bool = False, page: int = 1):
    require_room_access(request)
    if not is_host_request(request):
        client_ip = get_client_ip(request)
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

    search_fn = getattr(sys.modules.get("app"), "tiered_search", tiered_search)
    return await asyncio.to_thread(search_fn, q, force_online=force_online, sing_along=sing_along, page=page)


@router.get("/api/library")
async def api_get_library(
    request: Request,
    page: int = 1,
    limit: int = 25,
    sort: str = "popularity",
    artist: str = "",
    q: str = "",
    sing_along: bool = False
):
    require_room_access(request)
    data = get_browsable_tracks(page=page, limit=limit, sort=sort, artist=artist, search=q, sing_along=sing_along)
    data["artists"] = get_cached_unique_artists(sing_along=sing_along)
    return data


@router.get("/api/storage/status")
async def api_get_storage_status():
    return get_storage_status()


@router.get("/api/storage/cleanup-candidates")
async def api_get_cleanup_candidates(sort_by: str = "oldest_played", limit: int = 60, tab: str = "all"):
    return {
        "status": get_storage_status(),
        "candidates": get_cleanup_candidates(sort_by=sort_by, limit=limit, tab=tab)
    }


@router.post("/api/storage/prune")
async def api_prune_tracks(req: PruneTracksRequest, request: Request):
    if not is_host_request(request):
        raise HTTPException(status_code=403, detail="Host authentication required to prune files.")
    
    pruned_count = 0
    for tid in req.track_ids:
        if prune_local_track(tid, is_filler=False):
            pruned_count += 1
    for fid in (req.filler_ids or []):
        if prune_local_track(fid, is_filler=True):
            pruned_count += 1
    
    invalidate_library_cache()
    await manager.broadcast({
        "type": "library_updated",
        "queue": get_queue()
    })
    return {
        "status": "ok",
        "pruned_count": pruned_count,
        "storage": get_storage_status()
    }


@router.put("/api/host/tracks/{track_id}")
async def api_edit_track_metadata(track_id: int, req: EditTrackMetadataRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    success = edit_track_metadata(track_id, req.title, req.artist, is_filler=req.is_filler)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update track metadata")
    invalidate_library_cache()
    await manager.broadcast({
        "type": "library_updated",
        "queue": get_queue()
    })
    return {"status": "ok", "track_id": track_id, "title": req.title, "artist": req.artist}


@router.put("/api/host/tracks/{track_id}/convert-type")
async def api_convert_track_type(track_id: int, req: ConvertTrackTypeRequest, request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    res = convert_track_type(track_id, req.target_type, source_type=req.source_type, is_filler=req.is_filler)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Conversion failed"))
    invalidate_library_cache()
    await manager.broadcast({
        "type": "library_updated",
        "queue": get_queue()
    })
    return res


@router.post("/api/host/tracks/{track_id}/download")
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
