"""
Temporary Cache Service for Tiki Karaoke.
Handles automatic cleanup and pruning of video buffers in stream_only mode.
"""

from typing import Optional
from config import load_config
from database import (
    get_setting, get_queue, get_pending_downloads,
    get_track_by_id, prune_local_track
)


def maybe_cleanup_temporary_track(track_id: Optional[int]):
    """In Temporary Cache Mode (stream_only), deletes the buffered video file once no active queue items need it."""
    if not track_id:
        return
    profile = get_setting("video_quality_profile", "") or load_config().get("video_quality_profile", "pro")
    if profile != "stream_only":
        return
    try:
        remaining = get_queue()
        if any(q.get("track_id") == track_id and q.get("status") in ("pending", "playing") for q in remaining):
            return
        pending_dls = get_pending_downloads()
        if any(p.get("track_id") == track_id and p.get("status") in ("pending", "downloading") for p in pending_dls):
            return
        track = get_track_by_id(track_id)
        if not track:
            return
        if (
            track.get("media_source") == "imported"
            or str(track.get("video_id", "")).startswith("local_")
            or track.get("media_type") == "cdg"
            or bool(track.get("cdg_path"))
            or track.get("track_type") == "cdg"
        ):
            return
        prune_local_track(track_id, is_filler=False)
        print(f"[Temporary Cache Mode] Cleaned up temporary video buffer for track_id={track_id}")
    except Exception as e:
        print(f"[Temporary Cache Mode] Cleanup notice for track_id={track_id}: {e}")
