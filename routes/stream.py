"""
Media Streaming Router for Tiki Karaoke.
Handles range-aware HTTP audio/video streaming, CD+G graphic stream payloads,
and intermission background filler audio/video streams.
"""

import os
import mimetypes
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from database import get_track_by_id, get_filler_track_by_id

router = APIRouter()


@router.get("/stream/{track_id}")
async def stream_local_video(track_id: int):
    track = get_track_by_id(track_id)
    if not track or not track["is_downloaded"] or not track["local_path"]:
        raise HTTPException(status_code=404, detail="Track not available locally")
    
    file_path = track["local_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File missing on disk")

    mime, _ = mimetypes.guess_type(file_path)
    return FileResponse(file_path, media_type=mime or "video/mp4")


@router.get("/stream-cdg/{track_id}")
async def stream_local_cdg(track_id: int):
    track = get_track_by_id(track_id)
    if not track or not track.get("cdg_path"):
        raise HTTPException(status_code=404, detail="CDG lyrics not available for this track")
    
    cdg_path = track["cdg_path"]
    if not os.path.exists(cdg_path):
        raise HTTPException(status_code=404, detail="CDG file missing on disk")

    return FileResponse(cdg_path, media_type="application/octet-stream")


@router.get("/stream/filler/{filler_id}")
async def stream_filler_video(filler_id: int):
    track = get_filler_track_by_id(filler_id)
    if not track or not track["is_downloaded"] or not track["local_path"]:
        raise HTTPException(status_code=404, detail="Filler video not available locally")
    
    file_path = track["local_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Filler video file missing on disk")

    return FileResponse(file_path, media_type="video/mp4")
