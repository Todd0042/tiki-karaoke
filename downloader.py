import os
import sys
import re
import shutil
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Callable
import yt_dlp

from config import (
    get_tracks_dir, get_playlists_dir, get_filler_dir, get_sing_along_dir,
    TRACKS_DIR, PLAYLISTS_DIR, TARGET_LUFS, load_config, get_bundled_binary_path
)
from database import (
    get_track_by_id, update_track_download_status,
    get_filler_track_by_id, update_filler_track_status, update_filler_track_metadata,
    get_setting, update_pending_download_progress
)
from metadata import fetch_track_metadata

download_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
download_task_seq: int = 0
download_callbacks = []
download_progress_callbacks = []

filler_download_queue: asyncio.Queue = asyncio.Queue()
filler_callbacks = []

def register_download_callback(cb: Callable):
    download_callbacks.append(cb)

def register_download_progress_callback(cb: Callable):
    download_progress_callbacks.append(cb)

def register_filler_callback(cb: Callable):
    filler_callbacks.append(cb)

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else "unknown"

def get_ydl_video_format(quality_profile: str = "default") -> str:
    fmt_480p = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480][ext=mp4]/best[height<=480]/worst[ext=mp4]"
    fmt_720p = "bestvideo[height<=720][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]"
    fmt_1080p = "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    if quality_profile == "lowest":
        return "worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst"
    if quality_profile in ("medium_360p", "medium", "compact_480p", "inept", "stream_only"):
        return fmt_480p
    if quality_profile == "balanced_720p":
        return fmt_720p

    profile = get_setting("video_quality_profile", "") or load_config().get("video_quality_profile", "pro")
    if profile in ("compact_480p", "inept", "stream_only"):
        return fmt_480p
    if profile == "balanced_720p":
        return fmt_720p
    # Pro / default: 1080p Full HD
    return fmt_1080p

def normalize_audio_loudness(input_path: Path, output_path: Path, target_lufs: float = TARGET_LUFS) -> bool:
    ffmpeg_bin = str(get_bundled_binary_path("ffmpeg")) if get_bundled_binary_path("ffmpeg").exists() else "ffmpeg"
    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(input_path),
        "-c:v", "copy",
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path)
    ]
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, **kwargs)
        return res.returncode == 0
    except Exception as e:
        print(f"[Downloader] Loudnorm error: {e}")
        return False

def renormalize_local_track(track_id: int, target_lufs: float = TARGET_LUFS) -> bool:
    """Re-normalizes an existing local track audio stream to the specified target LUFS."""
    from database import get_track_by_id, update_track_lufs
    track = get_track_by_id(track_id)
    if not track or not track.get("local_path"):
        return False
    local_path = Path(track["local_path"])
    if not local_path.exists():
        return False

    temp_path = local_path.with_name(f"renorm_{track_id}_{local_path.name}")
    try:
        ok = normalize_audio_loudness(local_path, temp_path, target_lufs=target_lufs)
        if ok and temp_path.exists() and temp_path.stat().st_size > 1000:
            temp_path.replace(local_path)
            update_track_lufs(track_id, target_lufs)
            try:
                from services.library_cache import invalidate_library_cache
                invalidate_library_cache()
            except Exception:
                pass
            return True
        else:
            if temp_path.exists():
                temp_path.unlink()
            return False
    except Exception as e:
        print(f"[Downloader] Error renormalizing track {track_id}: {e}")
        if temp_path.exists():
            temp_path.unlink()
        return False

def download_and_normalize_track(
    track_id: int,
    pending_id: Optional[int] = None,
    progress_hook: Optional[Callable[[int, Optional[int], float, str], None]] = None,
    quality_profile: str = "default"
) -> Optional[str]:
    track = get_track_by_id(track_id)
    if not track:
        return None

    video_id = track["video_id"]
    if track["is_downloaded"] and track["local_path"] and Path(track["local_path"]).exists():
        return track["local_path"]

    if get_setting("strict_byom_only", "false") == "true":
        print(f"[Downloader] Strict Commercial / BYOM mode active: refusing download for track_id={track_id}")
        return None

    if track.get("duration") and track["duration"] > 600:
        print(f"[Downloader] Track {video_id} duration {track['duration']}s exceeds 600s limit. Aborting download.")
        update_track_download_status(track_id, "failed")
        return None

    update_track_download_status(track_id, "downloading")
    if progress_hook:
        progress_hook(track_id, pending_id, 0.0, "downloading")

    artist_clean = sanitize_filename(track["artist"])
    title_clean = sanitize_filename(track["title"])
    base_tracks_dir = get_sing_along_dir() if track.get("is_sing_along") else get_tracks_dir()
    artist_dir = base_tracks_dir / artist_clean
    artist_dir.mkdir(parents=True, exist_ok=True)

    temp_path = artist_dir / f"temp_{video_id}.mp4"
    final_path = artist_dir / f"{title_clean} [{video_id}].mp4"

    current_stream = [1]
    last_reported_percent = [-1.0]

    def ydl_progress_callback(d):
        if not progress_hook:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            if total > 0:
                stream_pct = (downloaded / total) * 100.0
                # Scale dual-stream download (stream 1: 0-60%, stream 2: 60-90%)
                if current_stream[0] == 1:
                    overall_pct = round(stream_pct * 0.60, 1)
                else:
                    overall_pct = round(60.0 + stream_pct * 0.30, 1)

                if overall_pct - last_reported_percent[0] >= 3.0 or overall_pct >= 89.0:
                    last_reported_percent[0] = overall_pct
                    progress_hook(track_id, pending_id, max(1.0, min(89.0, overall_pct)), "downloading")
        elif status == "finished":
            current_stream[0] += 1
            last_reported_percent[0] = max(last_reported_percent[0], 60.0)
            progress_hook(track_id, pending_id, 90.0, "normalizing")

    ydl_opts = {
        "format": get_ydl_video_format(quality_profile),
        "outtmpl": str(temp_path),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "match_filter": yt_dlp.utils.match_filter_func("duration <= 600"),
        "socket_timeout": 15,
        "retries": 3,
        "fragment_retries": 3,
        "skip_unavailable_fragments": True,
        "source_address": "0.0.0.0",  # Prevent 30-60s IPv6 timeouts
        "progress_hooks": [ydl_progress_callback]
    }
    if shutil.which("node"):
        ydl_opts["js_runtimes"] = {"node": {}}
    ffmpeg_bin = get_bundled_binary_path("ffmpeg")
    if ffmpeg_bin.exists():
        ydl_opts["ffmpeg_location"] = str(ffmpeg_bin.parent)

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not temp_path.exists():
            update_track_download_status(track_id, "failed")
            if progress_hook:
                progress_hook(track_id, pending_id, 0.0, "failed")
            return None

        # Apply FFmpeg Loudness Normalization (-16 LUFS)
        if progress_hook:
            progress_hook(track_id, pending_id, 92.0, "normalizing")
        success = normalize_audio_loudness(temp_path, final_path)
        if success and final_path.exists():
            if temp_path.exists():
                temp_path.unlink()
            local_file = str(final_path)
        else:
            # Fallback to temp file if normalization fails
            temp_path.rename(final_path)
            local_file = str(final_path)

        update_track_download_status(track_id, "ready", local_path=local_file, normalized_lufs=TARGET_LUFS)
        try:
            from services.library_cache import invalidate_library_cache
            invalidate_library_cache()
        except Exception:
            pass
        if progress_hook:
            progress_hook(track_id, pending_id, 100.0, "ready")
        return local_file

    except Exception as e:
        print(f"[Downloader] Error downloading video {video_id}: {e}")
        update_track_download_status(track_id, "failed")
        if progress_hook:
            progress_hook(track_id, pending_id, 0.0, "failed")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return None

def update_singer_m3u_playlist(singer_name: str, local_path: str, title: str, artist: str):
    clean_name = sanitize_filename(singer_name)
    m3u_path = get_playlists_dir() / f"{clean_name}.m3u8"
    
    is_new = not m3u_path.exists()
    with open(m3u_path, "a", encoding="utf-8") as f:
        if is_new:
            f.write("#EXTM3U\n")
        f.write(f"#EXTINF:-1,{artist} - {title}\n")
        f.write(f"{local_path}\n")

async def downloader_worker():
    while True:
        priority, seq, task = await download_queue.get()
        track_id = task.get("track_id")
        pending_id = task.get("pending_id")
        singer_name = task.get("singer_name", "")
        title = task.get("title", "")
        artist = task.get("artist", "")
        quality_profile = task.get("quality_profile", "default")

        loop = asyncio.get_running_loop()

        def sync_progress_hook(tid, pid, pct, status):
            if pid:
                try:
                    update_pending_download_progress(pid, pct, status)
                except Exception:
                    pass
            for cb in download_progress_callbacks:
                try:
                    res = cb(tid, pid, pct, status)
                    if asyncio.iscoroutine(res):
                        asyncio.run_coroutine_threadsafe(res, loop)
                except Exception as e:
                    print(f"[Downloader] Progress callback error: {e}")

        try:
            try:
                local_path = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        download_and_normalize_track,
                        track_id,
                        pending_id,
                        sync_progress_hook,
                        quality_profile
                    ),
                    timeout=300
                )
            except asyncio.TimeoutError:
                print(f"[Downloader] Track download timed out after 300s for track_id={track_id}")
                update_track_download_status(track_id, "failed")
                if pending_id:
                    try:
                        update_pending_download_progress(pending_id, 0.0, "failed", "Download timed out")
                    except Exception:
                        pass
                local_path = None

            active_profile = get_setting("video_quality_profile", "") or load_config().get("video_quality_profile", "pro")
            if local_path and singer_name and active_profile != "stream_only" and quality_profile != "stream_only":
                update_singer_m3u_playlist(singer_name, local_path, title, artist)

            for cb in download_callbacks:
                try:
                    res = cb(track_id, local_path, pending_id)
                    if asyncio.iscoroutine(res):
                        await res
                except TypeError:
                    try:
                        res = cb(track_id, local_path)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        print(f"[Downloader] Callback error: {e}")
                except Exception as e:
                    print(f"[Downloader] Callback error: {e}")
        except Exception as e:
            print(f"[Downloader] Worker error: {e}")
        finally:
            download_queue.task_done()

def enqueue_download(
    track_id: int,
    singer_name: str = "",
    title: str = "",
    artist: str = "",
    pending_id: Optional[int] = None,
    quality_profile: str = "default",
    priority: int = 10
):
    global download_task_seq
    if get_setting("strict_byom_only", "false") == "true":
        print(f"[Downloader] Strict Commercial / BYOM mode active: skipping download enqueue for track_id={track_id}")
        return
    download_task_seq += 1
    download_queue.put_nowait((priority, download_task_seq, {
        "track_id": track_id,
        "pending_id": pending_id,
        "singer_name": singer_name,
        "title": title,
        "artist": artist,
        "quality_profile": quality_profile
    }))

def download_and_normalize_filler_track(filler_id: int) -> Optional[str]:
    track = get_filler_track_by_id(filler_id)
    if not track:
        return None

    video_id = track["video_id"]
    if track["is_downloaded"] and track["local_path"] and Path(track["local_path"]).exists():
        return track["local_path"]

    # 1. Fetch metadata if genre or release_year missing
    if not track.get("genre") or not track.get("release_year"):
        update_filler_track_status(filler_id, "fetching_metadata")
        try:
            meta = fetch_track_metadata(track["artist"], track["title"])
            if meta:
                update_filler_track_metadata(
                    filler_id,
                    genre=meta.get("genre", "Pop"),
                    release_year=meta.get("release_year", 0),
                    artist=meta.get("artist") or track["artist"],
                    title=meta.get("title") or track["title"]
                )
                track = get_filler_track_by_id(filler_id) or track
        except Exception as e:
            print(f"[Filler Downloader] Metadata fetch error: {e}")

    # Check duration limit (10 minutes)
    if track.get("duration") and track["duration"] > 600:
        print(f"[Filler Downloader] Filler {video_id} duration {track['duration']}s exceeds 600s limit.")
        update_filler_track_status(filler_id, "failed", error_message="Exceeds 10 minute limit")
        return None

    update_filler_track_status(filler_id, "downloading")

    filler_dir = get_filler_dir()
    filler_dir.mkdir(parents=True, exist_ok=True)

    title_clean = sanitize_filename(track["title"])
    artist_clean = sanitize_filename(track["artist"])
    temp_path = filler_dir / f"temp_{video_id}.mp4"
    final_path = filler_dir / f"{artist_clean} - {title_clean} [{video_id}].mp4"

    ydl_opts = {
        "format": get_ydl_video_format(),
        "outtmpl": str(temp_path),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "match_filter": yt_dlp.utils.match_filter_func("duration <= 600"),
        "socket_timeout": 15,
        "retries": 3,
        "fragment_retries": 3,
        "skip_unavailable_fragments": True,
        "source_address": "0.0.0.0",  # Prevent 30-60s IPv6 timeouts
    }
    if shutil.which("node"):
        ydl_opts["js_runtimes"] = {"node": {}}
    ffmpeg_bin = get_bundled_binary_path("ffmpeg")
    if ffmpeg_bin.exists():
        ydl_opts["ffmpeg_location"] = str(ffmpeg_bin.parent)

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not temp_path.exists():
            update_filler_track_status(filler_id, "failed", error_message="Download produced no file")
            return None

        update_filler_track_status(filler_id, "normalizing")
        # Apply FFmpeg Loudness Normalization (-16 LUFS)
        success = normalize_audio_loudness(temp_path, final_path)
        if success and final_path.exists():
            if temp_path.exists():
                temp_path.unlink()
            local_file = str(final_path)
        else:
            temp_path.rename(final_path)
            local_file = str(final_path)

        update_filler_track_status(filler_id, "ready", local_path=local_file, is_downloaded=1)
        return local_file

    except Exception as e:
        print(f"[Filler Downloader] Error downloading video {video_id}: {e}")
        update_filler_track_status(filler_id, "failed", error_message=str(e))
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return None

def enqueue_filler_download(filler_id: int, pending_id: Optional[int] = None):
    filler_download_queue.put_nowait((filler_id, pending_id))

async def filler_downloader_worker():
    while True:
        task = await filler_download_queue.get()
        if isinstance(task, tuple):
            filler_id, pending_id = task
        else:
            filler_id, pending_id = task, None

        if pending_id:
            try:
                update_pending_download_progress(pending_id, 15.0, "downloading")
            except Exception:
                pass

        try:
            loop = asyncio.get_event_loop()
            try:
                local_path = await asyncio.wait_for(
                    loop.run_in_executor(None, download_and_normalize_filler_track, filler_id),
                    timeout=300
                )
            except asyncio.TimeoutError:
                print(f"[Filler Downloader] Download timed out after 300s for filler_id={filler_id}")
                update_filler_track_status(filler_id, "failed", error_message="Download timed out after 300s")
                if pending_id:
                    try:
                        update_pending_download_progress(pending_id, 0.0, "failed", "Download timed out")
                    except Exception:
                        pass
                local_path = None

            for cb in filler_callbacks:
                try:
                    res = cb(filler_id, local_path, pending_id)
                    if asyncio.iscoroutine(res):
                        await res
                except TypeError:
                    try:
                        res = cb(filler_id, local_path)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        print(f"[Filler Downloader] Callback error: {e}")
                except Exception as e:
                    print(f"[Filler Downloader] Callback error: {e}")
        except Exception as e:
            print(f"[Filler Downloader] Worker error: {e}")
        finally:
            filler_download_queue.task_done()

