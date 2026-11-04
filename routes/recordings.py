"""
routes/recordings.py - Live Singer Performance Recording endpoints.

Provides functionality for:
- Uploading recorded vocal performances from mobile/stage audio DSP
- Converting raw WebM audio to ID3-tagged MP3 files via ffmpeg
- Streaming and downloading saved recordings
- Managing host storage directory and opening recordings folder in file manager
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import (
    BAR_NAME,
    get_bundled_binary_path,
    get_default_recordings_dir,
    get_recordings_dir,
    save_config,
)
from database import (
    delete_recording,
    get_all_recordings,
    get_db,
    get_recordings_for_singer,
    get_singer_by_token,
    get_track_by_id,
    save_recording,
    set_setting,
)
from services.auth import is_host_request, is_local_request
from services.websocket_manager import manager

router = APIRouter(tags=["recordings"])


class HostUpdateRecordingsPathRequest(BaseModel):
    recordings_dir: str = ""


@router.post("/api/recordings/upload")
async def api_upload_recording(
    file: UploadFile = File(...),
    queue_id: Optional[int] = Form(None),
    singer_id: int = Form(...),
    track_id: int = Form(...),
    duration: float = Form(0.0),
    request: Request = None,
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
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, **kwargs)
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


@router.get("/api/recordings/{recording_id}/stream")
async def api_stream_recording(recording_id: int):
    conn = get_db()
    row = conn.execute("SELECT file_path, filename FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    conn.close()
    if not row or not Path(row["file_path"]).exists():
        raise HTTPException(status_code=404, detail="Recording file not found")
    media_type = "audio/mpeg" if row["filename"].endswith(".mp3") else "audio/webm"
    return FileResponse(row["file_path"], media_type=media_type)


@router.get("/api/recordings/{recording_id}/download")
async def api_download_recording(recording_id: int):
    conn = get_db()
    row = conn.execute("SELECT file_path, filename FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    conn.close()
    if not row or not Path(row["file_path"]).exists():
        raise HTTPException(status_code=404, detail="Recording file not found")
    return FileResponse(row["file_path"], filename=row["filename"], media_type="application/octet-stream")


@router.get("/api/singer/{token}/recordings")
async def api_get_singer_recordings(token: str):
    singer = get_singer_by_token(token)
    if not singer:
        return []
    return get_recordings_for_singer(singer["id"])


@router.get("/api/host/recordings")
async def api_get_host_recordings(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    return get_all_recordings()


@router.delete("/api/host/recordings/{recording_id}")
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


@router.get("/api/host/storage/recordings")
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


@router.post("/api/host/storage/recordings")
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


@router.post("/api/host/storage/recordings/open-folder")
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
