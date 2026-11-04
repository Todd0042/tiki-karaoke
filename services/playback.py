"""
services/playback.py - Background playback transitions, filler video cycles, and download notifications.

Manages:
- Intermission countdowns and smooth transitions between live singers and BGM filler
- BGM filler music video rotation, buffer countdowns, and fading
- In-flight yt-dlp download callbacks and retry escalation ladders
- CD+G zip extraction helper
"""

import asyncio
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config import DEFAULT_INTERMISSION_SECONDS
from database import (
    get_current_playing,
    get_filler_track_by_id,
    get_next_vibe_filler_track,
    get_pending_download_by_filler_id,
    get_pending_download_by_id,
    get_pending_download_by_track_id,
    get_pending_downloads,
    get_queue,
    get_setting,
    increment_pending_download_retry,
    promote_pending_download_to_queue,
    record_filler_play,
    remove_pending_download,
    update_pending_download_progress,
)
from downloader import (
    enqueue_download,
    register_download_callback,
    register_download_progress_callback,
    register_filler_callback,
)
from services.temporary_cache import maybe_cleanup_temporary_track
from services.websocket_manager import manager

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


def get_intermission_end() -> Optional[float]:
    import app
    return getattr(app, "current_intermission_end", None)


def set_intermission_end(val: Optional[float]) -> None:
    import app
    app.current_intermission_end = val


def extract_cdg_zip(zip_path: Path) -> Optional[Tuple[str, str]]:
    """
    Extracts a CD+G .zip file containing matching .cdg and audio file (.mp3/.wav/.ogg)
    into a subfolder named after the zip. Returns (audio_path, cdg_path) or None.
    """
    try:
        if not zipfile.is_zipfile(zip_path):
            return None
        target_dir = zip_path.parent / f"_extracted_{zip_path.stem}"
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(target_dir)

        cdg_file = None
        audio_file = None
        for f in target_dir.glob("*"):
            if f.is_file():
                if f.suffix.lower() == ".cdg" and not cdg_file:
                    cdg_file = f
                elif f.suffix.lower() in (".mp3", ".wav", ".ogg", ".m4a", ".flac") and not audio_file:
                    audio_file = f

        if cdg_file and audio_file:
            return (str(audio_file), str(cdg_file))
        return None
    except Exception as e:
        print(f"[CDG Extract] Error extracting {zip_path.name}: {e}")
        return None


async def trigger_intermission_if_idle():
    """
    If nothing is currently playing and a stage display is connected, triggers smooth transition
    from ambient filler into intermission countdown for the next song in queue.
    """
    global filler_active, filler_transition_state, filler_buffer_task
    current_playing = get_current_playing()
    if current_playing:
        return

    queue = get_queue()
    if not queue:
        return

    cur_end = get_intermission_end()
    # If an intermission countdown is already actively running, do not clobber it
    if cur_end and time.time() < cur_end:
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
                    global filler_active, filler_transition_state
                    try:
                        await asyncio.sleep(3)
                        filler_active = False
                        filler_transition_state = "idle"
                        dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
                        set_intermission_end(time.time() + dur)
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
    set_intermission_end(time.time() + dur)
    await manager.broadcast({
        "type": "start_intermission",
        "next_track": next_song_item,
        "duration": dur
    })


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
    global filler_active, filler_transition_state, filler_current_track
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending" and q.get("singer_status", "active") == "active"]

    if pending:
        filler_active = False
        filler_transition_state = "idle"
        filler_current_track = None

        dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
        set_intermission_end(time.time() + dur)
        await manager.broadcast({"type": "stop_filler"})
        await manager.broadcast({
            "type": "start_intermission",
            "next_track": pending[0],
            "duration": dur
        })
    else:
        filler_active = False
        filler_transition_state = "idle"
        filler_current_track = None
        await trigger_filler_sequence()


def parse_karaoke_filename(filename: str) -> Tuple[str, str]:
    import re
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
    import shutil
    import subprocess
    from config import get_bundled_binary_path
    ffprobe_bin = "ffprobe" if shutil.which("ffprobe") else str(get_bundled_binary_path("ffprobe"))
    if not (shutil.which("ffprobe") or os.path.exists(ffprobe_bin)):
        return 210
    try:
        cmd = [
            ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, **kwargs)
        return int(float(res.stdout.strip()))
    except Exception:
        return 210


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
            print(f"[Metadata Enricher] Automatically enriched {updated_count} media tracks with genre/year tags.")
    except Exception as e:
        print(f"[Metadata Enricher] Background enrichment failed: {e}")

