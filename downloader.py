import os
import re
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Callable
import yt_dlp

from config import get_tracks_dir, get_playlists_dir, TRACKS_DIR, PLAYLISTS_DIR, TARGET_LUFS
from database import get_track_by_id, update_track_download_status

download_queue: asyncio.Queue = asyncio.Queue()
download_callbacks = []

def register_download_callback(cb: Callable):
    download_callbacks.append(cb)

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else "unknown"

def normalize_audio_loudness(input_path: Path, output_path: Path, target_lufs: float = TARGET_LUFS) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-c:v", "copy",
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return res.returncode == 0
    except Exception as e:
        print(f"[Downloader] Loudnorm error: {e}")
        return False

def download_and_normalize_track(track_id: int) -> Optional[str]:
    track = get_track_by_id(track_id)
    if not track:
        return None

    video_id = track["video_id"]
    if track["is_downloaded"] and track["local_path"] and Path(track["local_path"]).exists():
        return track["local_path"]

    update_track_download_status(track_id, "downloading")

    artist_clean = sanitize_filename(track["artist"])
    title_clean = sanitize_filename(track["title"])
    artist_dir = get_tracks_dir() / artist_clean
    artist_dir.mkdir(parents=True, exist_ok=True)

    temp_path = artist_dir / f"temp_{video_id}.mp4"
    final_path = artist_dir / f"{title_clean} [{video_id}].mp4"

    ydl_opts = {
        "format": "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(temp_path),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4"
    }

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not temp_path.exists():
            update_track_download_status(track_id, "failed")
            return None

        # Apply FFmpeg Loudness Normalization (-16 LUFS)
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
        return local_file

    except Exception as e:
        print(f"[Downloader] Error downloading video {video_id}: {e}")
        update_track_download_status(track_id, "failed")
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
        task = await download_queue.get()
        track_id = task.get("track_id")
        singer_name = task.get("singer_name", "")
        title = task.get("title", "")
        artist = task.get("artist", "")

        try:
            loop = asyncio.get_event_loop()
            local_path = await loop.run_in_executor(None, download_and_normalize_track, track_id)
            if local_path and singer_name:
                update_singer_m3u_playlist(singer_name, local_path, title, artist)

            for cb in download_callbacks:
                try:
                    await cb(track_id, local_path)
                except Exception as e:
                    print(f"[Downloader] Callback error: {e}")
        except Exception as e:
            print(f"[Downloader] Worker error: {e}")
        finally:
            download_queue.task_done()

def enqueue_download(track_id: int, singer_name: str = "", title: str = "", artist: str = ""):
    download_queue.put_nowait({
        "track_id": track_id,
        "singer_name": singer_name,
        "title": title,
        "artist": artist
    })
