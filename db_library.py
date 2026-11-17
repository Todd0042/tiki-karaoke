import datetime
import hashlib
import re
import secrets
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import (
    DB_PATH, ensure_storage_dirs, get_data_dir, get_db_path,
    get_filler_dir, get_sing_along_dir, get_tracks_dir,
)
from db_schema import get_db

def save_track(
    video_id: str,
    title: str,
    artist: str,
    original_title: str,
    channel: str = "",
    duration: int = 0,
    is_sing_along: bool = False,
    media_source: str = "youtube",
    media_type: str = "video",
    cdg_path: str = "",
    is_starter_pack: int = 0,
    starter_pack_name: str = ""
) -> int:
    conn = get_db()
    if video_id.startswith("local_") and media_source == "youtube":
        media_source = "imported"
    with conn:
        cursor = conn.execute("""
            INSERT INTO tracks (video_id, title, artist, original_title, channel, duration, is_sing_along, media_source, media_type, cdg_path, is_starter_pack, starter_pack_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title = CASE WHEN tracks.is_downloaded = 1 THEN tracks.title ELSE excluded.title END,
                artist = CASE WHEN tracks.is_downloaded = 1 THEN tracks.artist ELSE excluded.artist END,
                original_title = excluded.original_title,
                channel = excluded.channel,
                duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE tracks.duration END,
                is_sing_along = CASE WHEN excluded.is_sing_along = 1 THEN 1 ELSE tracks.is_sing_along END,
                media_source = CASE WHEN tracks.media_source = 'imported' THEN 'imported' ELSE excluded.media_source END,
                media_type = CASE WHEN excluded.media_type != '' THEN excluded.media_type ELSE tracks.media_type END,
                cdg_path = CASE WHEN excluded.cdg_path != '' THEN excluded.cdg_path ELSE tracks.cdg_path END,
                is_starter_pack = CASE WHEN excluded.is_starter_pack = 1 THEN 1 ELSE tracks.is_starter_pack END,
                starter_pack_name = CASE WHEN excluded.starter_pack_name != '' THEN excluded.starter_pack_name ELSE tracks.starter_pack_name END
        """, (video_id, title, artist, original_title, channel, duration, 1 if is_sing_along else 0, media_source, media_type, cdg_path, is_starter_pack, starter_pack_name))
        
        cursor = conn.execute("SELECT id FROM tracks WHERE video_id = ?", (video_id,))
        track_id = cursor.fetchone()["id"]
    conn.close()
    return track_id

def get_track_by_id(track_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_track_by_video_id(video_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("SELECT * FROM tracks WHERE video_id = ?", (video_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_track_download_status(track_id: int, status: str, local_path: str = "", normalized_lufs: float = -16.0, cdg_path: str = ""):
    conn = get_db()
    with conn:
        is_dl = 1 if status == "ready" and local_path else 0
        conn.execute("""
            UPDATE tracks
            SET download_status = ?,
                is_downloaded = CASE WHEN ? = 1 THEN 1 ELSE is_downloaded END,
                local_path = CASE WHEN ? != '' THEN ? ELSE local_path END,
                cdg_path = CASE WHEN ? != '' THEN ? ELSE cdg_path END,
                normalized_lufs = ?
            WHERE id = ?
        """, (status, is_dl, local_path, local_path, cdg_path, cdg_path, normalized_lufs, track_id))
    conn.close()

def set_track_sing_along(track_id: int, is_sing_along: bool = True):
    conn = get_db()
    with conn:
        conn.execute("UPDATE tracks SET is_sing_along = ? WHERE id = ?", (1 if is_sing_along else 0, track_id))
    conn.close()

def update_track_metadata(track_id: int, genre: str = "", release_year: int = 0):
    conn = get_db()
    with conn:
        conn.execute("""
            UPDATE tracks
            SET genre = CASE WHEN ? != '' THEN ? ELSE genre END,
                release_year = CASE WHEN ? > 0 THEN ? ELSE release_year END
            WHERE id = ?
        """, (genre, genre, release_year, release_year, track_id))
    conn.close()

def update_track_lufs(track_id: int, normalized_lufs: float):
    conn = get_db()
    with conn:
        conn.execute("UPDATE tracks SET normalized_lufs = ? WHERE id = ?", (normalized_lufs, track_id))
    conn.close()

def get_tracks_needing_renormalization(target_lufs: float = -16.0, tolerance: float = 0.5) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT id, video_id, title, artist, local_path, normalized_lufs
        FROM tracks
        WHERE is_downloaded = 1 AND local_path != '' AND (abs(coalesce(normalized_lufs, 0) - ?) > ?)
        ORDER BY artist ASC, title ASC
    """, (target_lufs, tolerance))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def import_local_track(
    file_path: str,
    title: str,
    artist: str,
    duration: int = 0,
    is_sing_along: bool = False,
    media_type: str = "video",
    cdg_path: str = ""
) -> int:
    """Imports an existing local video or karaoke track into the Tiki library."""
    resolved = str(Path(file_path).resolve())
    resolved_cdg = str(Path(cdg_path).resolve()) if cdg_path else ""
    vid = f"local_{hashlib.sha256((resolved + resolved_cdg).encode()).hexdigest()[:12]}"
    track_id = save_track(
        video_id=vid,
        title=title,
        artist=artist,
        original_title=Path(file_path).name,
        channel="Local Library",
        duration=duration,
        is_sing_along=is_sing_along,
        media_source="imported",
        media_type=media_type,
        cdg_path=resolved_cdg
    )
    update_track_download_status(track_id, status="ready", local_path=resolved, normalized_lufs=-16.0, cdg_path=resolved_cdg)
    return track_id

def search_local_tracks(query: str, limit: int = 25, sing_along_only: bool = False) -> List[Dict[str, Any]]:
    conn = get_db()
    target_sa = 1 if sing_along_only else 0
    clean_q = "".join(c for c in query if c.isalnum() or c.isspace()).strip()

    strict_byom = get_setting("strict_byom_only", "false") == "true"
    strict_filter_simple = " AND (media_source = 'imported' OR video_id LIKE 'local_%')" if strict_byom else ""
    strict_filter_joined = " AND (t.media_source = 'imported' OR t.video_id LIKE 'local_%')" if strict_byom else ""

    if not clean_q:
        cursor = conn.execute(f"SELECT * FROM tracks WHERE is_downloaded = 1 AND coalesce(is_sing_along, 0) = ?{strict_filter_simple} ORDER BY id DESC LIMIT ?", (target_sa, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    fts_query = f'"{clean_q}"* OR ' + " ".join(f'"{word}"*' for word in clean_q.split())
    try:
        cursor = conn.execute(f"""
            SELECT t.*, rank
            FROM tracks t
            JOIN tracks_fts f ON t.id = f.rowid
            WHERE tracks_fts MATCH ? AND t.is_downloaded = 1 AND coalesce(t.is_sing_along, 0) = ?{strict_filter_joined}
            ORDER BY rank
            LIMIT ?
        """, (fts_query, target_sa, limit))
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        like_str = f"%{clean_q}%"
        cursor = conn.execute(f"""
            SELECT * FROM tracks
            WHERE is_downloaded = 1 AND coalesce(is_sing_along, 0) = ?{strict_filter_simple} AND (title LIKE ? OR artist LIKE ? OR original_title LIKE ?)
            LIMIT ?
        """, (target_sa, like_str, like_str, like_str, limit))
        rows = cursor.fetchall()

    conn.close()
    return [dict(r) for r in rows]

# Queue Operations & Fair Rotation

def get_singer_history(singer_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            h.id,
            h.played_at,
            h.pitch_shift,
            h.duet_partner,
            t.title,
            t.artist,
            t.video_id,
            t.duration
        FROM history h
        JOIN tracks t ON h.track_id = t.id
        WHERE h.singer_id = ?
        ORDER BY h.played_at DESC
    """, (singer_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_singer_songbook(singer_id: int) -> List[Dict[str, Any]]:
    """Returns all unique songs queued by this singer across sessions, ranked by queue frequency."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            t.id as track_id,
            t.video_id,
            t.title,
            t.artist,
            t.duration,
            t.is_downloaded,
            COUNT(l.id) as times_queued,
            MAX(l.created_at) as last_queued_at,
            (
                SELECT pitch_shift 
                FROM singer_song_log 
                WHERE singer_id = l.singer_id AND track_id = l.track_id 
                ORDER BY id DESC LIMIT 1
            ) as preferred_pitch
        FROM singer_song_log l
        JOIN tracks t ON l.track_id = t.id
        WHERE l.singer_id = ?
        GROUP BY t.id, t.video_id, t.title, t.artist, t.duration, t.is_downloaded
        ORDER BY times_queued DESC, last_queued_at DESC
    """, (singer_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_party_wrapped_stats() -> Dict[str, Any]:
    conn = get_db()
    top_singers = conn.execute("""
        SELECT s.name, s.avatar, COUNT(h.id) as song_count
        FROM history h
        JOIN singers s ON h.singer_id = s.id
        GROUP BY s.id
        ORDER BY song_count DESC
        LIMIT 3
    """).fetchall()

    top_songs = conn.execute("""
        SELECT t.title, t.artist, COUNT(h.id) as count
        FROM history h
        JOIN tracks t ON h.track_id = t.id
        GROUP BY t.id
        ORDER BY count DESC
        LIMIT 3
    """).fetchall()

    total_songs = conn.execute("SELECT COUNT(*) as total FROM history").fetchone()["total"]

    conn.close()
    return {
        "total_songs": total_songs,
        "top_singers": [dict(r) for r in top_singers],
        "top_songs": [dict(r) for r in top_songs]
    }

# Settings Operations
def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    cursor = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    with conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.close()

def get_all_settings() -> Dict[str, str]:
    conn = get_db()
    cursor = conn.execute("SELECT key, value FROM settings")
    rows = cursor.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

def migrate_storage_paths(old_base_dir: str, new_base_dir: str) -> int:
    """Updates local_path in tracks table if the library location was moved."""
    old_base = str(old_base_dir).rstrip("/\\")
    new_base = str(new_base_dir).rstrip("/\\")
    if not old_base or not new_base or old_base == new_base:
        return 0
    conn = get_db()
    with conn:
        cursor = conn.execute(
            "UPDATE tracks SET local_path = replace(local_path, ?, ?) WHERE local_path LIKE ?",
            (old_base, new_base, f"{old_base}%")
        )
        count = cursor.rowcount
    conn.close()
    return count

# Library Browsing & Storage Management
def get_browsable_tracks(
    page: int = 1,
    limit: int = 25,
    sort: str = "popularity",
    artist: str = "",
    search: str = "",
    sing_along: bool = False
) -> Dict[str, Any]:
    """Returns paginated downloaded tracks with custom sorting: popularity, bring_back, recent, least_played, alpha."""
    conn = get_db()
    target_sa = 1 if sing_along else 0
    conditions = ["is_downloaded = 1", "coalesce(is_sing_along, 0) = ?"]
    params: List[Any] = [target_sa]

    strict_byom = get_setting("strict_byom_only", "false") == "true"
    if strict_byom:
        conditions.append("(media_source = 'imported' OR video_id LIKE 'local_%')")

    if artist.strip():
        conditions.append("artist = ?")
        params.append(artist.strip())

    if search.strip():
        s = f"%{search.strip()}%"
        conditions.append("(title LIKE ? OR artist LIKE ?)")
        params.extend([s, s])

    # Bring It Back algorithm: songs with at least 5 plays sorted by longest time since played
    # If the database has fewer total plays (e.g. party just started), fallback to play_count >= 1
    if sort == "bring_back":
        bb_conds = list(conditions)
        bb_params = list(params)
        bb_conds_5 = bb_conds + ["play_count >= 5"]
        cnt_5 = conn.execute(f"SELECT COUNT(*) as c FROM tracks WHERE {' AND '.join(bb_conds_5)}", bb_params).fetchone()["c"]
        if cnt_5 > 0:
            conditions.append("play_count >= 5")
            order_clause = "ORDER BY last_played ASC, play_count DESC, title ASC"
        else:
            conditions.append("play_count >= 1")
            order_clause = "ORDER BY last_played ASC, play_count DESC, title ASC"
    elif sort == "popularity":
        order_clause = "ORDER BY play_count DESC, last_played DESC, title ASC"
    elif sort == "recent":
        order_clause = "ORDER BY last_played DESC, play_count DESC"
    elif sort == "least_played":
        order_clause = "ORDER BY play_count ASC, coalesce(last_played, created_at) ASC"
    elif sort == "alpha":
        order_clause = "ORDER BY artist COLLATE NOCASE ASC, title COLLATE NOCASE ASC"
    else:
        order_clause = "ORDER BY play_count DESC, title ASC"

    where_clause = " WHERE " + " AND ".join(conditions)

    count_query = f"SELECT COUNT(*) as total FROM tracks {where_clause}"
    total = conn.execute(count_query, params).fetchone()["total"]

    offset = max(0, (page - 1) * limit)
    data_query = f"""
        SELECT id, video_id, title, artist, original_title, channel, duration,
               is_downloaded, local_path, normalized_lufs, download_status,
               created_at, coalesce(play_count, 0) as play_count, last_played,
               coalesce(is_sing_along, 0) as is_sing_along,
               coalesce(media_source, 'youtube') as media_source
        FROM tracks
        {where_clause}
        {order_clause}
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(data_query, params + [limit, offset]).fetchall()
    conn.close()

    total_pages = max(1, (total + limit - 1) // limit) if total > 0 else 1
    return {
        "tracks": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "sort": sort,
        "artist": artist,
        "search": search
    }

def get_unique_downloaded_artists(sing_along: bool = False) -> List[str]:
    """Returns distinct artists who have downloaded tracks in the library."""
    conn = get_db()
    target_sa = 1 if sing_along else 0
    rows = conn.execute("""
        SELECT DISTINCT artist 
        FROM tracks 
        WHERE is_downloaded = 1 AND artist != '' AND coalesce(is_sing_along, 0) = ?
        ORDER BY artist COLLATE NOCASE ASC
    """, (target_sa,)).fetchall()
    conn.close()
    return [r["artist"] for r in rows]

def sanitize_db_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else "unknown"

def edit_track_metadata(track_id: int, new_title: str, new_artist: str, is_filler: bool = False) -> bool:
    """Updates title and artist metadata for a track or filler, and safely renames its physical video file on disk."""
    new_title = new_title.strip()
    new_artist = new_artist.strip()
    if not new_title or not new_artist:
        return False

    conn = get_db()
    table = "filler_tracks" if is_filler else "tracks"
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (track_id,)).fetchone()
    if not row:
        conn.close()
        return False

    item = dict(row)
    local_path = item.get("local_path", "")
    new_local_path = local_path
    video_id = item.get("video_id", "")

    # Rename physical file on disk if it exists
    if local_path and Path(local_path).exists():
        old_path = Path(local_path)
        clean_title = sanitize_db_filename(new_title)
        clean_artist = sanitize_db_filename(new_artist)

        if is_filler:
            target_dir = get_filler_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / f"{clean_artist} - {clean_title} [{video_id}].mp4"
        else:
            base_dir = get_sing_along_dir() if item.get("is_sing_along") else get_tracks_dir()
            target_dir = base_dir / clean_artist
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / f"{clean_title} [{video_id}].mp4"

        try:
            if old_path.resolve() != target_file.resolve():
                shutil.move(str(old_path), str(target_file))
                new_local_path = str(target_file)
        except Exception as e:
            print(f"[Edit Metadata] Error renaming file from {old_path} to {target_file}: {e}")

    with conn:
        conn.execute(f"""
            UPDATE {table}
            SET title = ?, artist = ?, local_path = ?
            WHERE id = ?
        """, (new_title, new_artist, new_local_path, track_id))
    conn.close()
    return True

def convert_track_type(track_id: int, target_type: str, source_type: str = "auto", is_filler: bool = False) -> Dict[str, Any]:
    """
    Failsafe song type converter (Option A: Physical File Relocation).
    Converts between 'karaoke', 'sing_along', and 'music_video'.
    Relocates the .mp4 file to the appropriate folder and updates the database record.
    """
    valid_types = {"karaoke", "sing_along", "music_video"}
    if target_type not in valid_types:
        return {"success": False, "error": f"Invalid target type '{target_type}'"}

    conn = get_db()
    
    # 1. Determine source item
    if is_filler or source_type == "music_video":
        row = conn.execute("SELECT * FROM filler_tracks WHERE id = ?", (track_id,)).fetchone()
        source_is_filler = True
    else:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        source_is_filler = False
        if not row:
            # Fallback check filler
            row = conn.execute("SELECT * FROM filler_tracks WHERE id = ?", (track_id,)).fetchone()
            source_is_filler = bool(row)

    if not row:
        conn.close()
        return {"success": False, "error": "Track not found"}

    item = dict(row)
    video_id = item["video_id"]
    title = item["title"]
    artist = item["artist"]
    local_path = item.get("local_path", "")
    old_file_exists = bool(local_path and Path(local_path).exists())
    old_path = Path(local_path) if old_file_exists else None

    clean_artist = sanitize_db_filename(artist)
    clean_title = sanitize_db_filename(title)
    new_local_path = local_path

    # Compute target file destination
    if target_type == "music_video":
        target_dir = get_filler_dir()
        target_file = target_dir / f"{clean_artist} - {clean_title} [{video_id}].mp4"
    elif target_type == "sing_along":
        target_dir = get_sing_along_dir() / clean_artist
        target_file = target_dir / f"{clean_title} [{video_id}].mp4"
    else: # karaoke
        target_dir = get_tracks_dir() / clean_artist
        target_file = target_dir / f"{clean_title} [{video_id}].mp4"

    # Option A: Relocate physical file if present
    if old_file_exists:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if old_path.resolve() != target_file.resolve():
                shutil.move(str(old_path), str(target_file))
            new_local_path = str(target_file)
        except Exception as e:
            print(f"[Convert Type] Error relocating file from {old_path} to {target_file}: {e}")
            new_local_path = str(target_file)
    else:
        new_local_path = str(target_file) if local_path else ""

    # Database transition
    new_track_id = track_id
    new_is_filler = False

    with conn:
        if source_is_filler and target_type in ("karaoke", "sing_along"):
            # Migrating from filler_tracks to tracks
            is_sa = 1 if target_type == "sing_along" else 0
            cur = conn.execute("""
                INSERT INTO tracks (
                    video_id, title, artist, original_title, duration, is_downloaded,
                    local_path, download_status, is_sing_along, is_starter_pack, starter_pack_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title = excluded.title,
                    artist = excluded.artist,
                    is_downloaded = excluded.is_downloaded,
                    local_path = excluded.local_path,
                    download_status = excluded.download_status,
                    is_sing_along = excluded.is_sing_along,
                    is_starter_pack = excluded.is_starter_pack,
                    starter_pack_name = excluded.starter_pack_name
            """, (
                video_id, title, artist, item.get("original_title", f"{artist} - {title}"),
                item.get("duration", 0), 1 if old_file_exists else 0, new_local_path,
                is_sa, item.get("is_starter_pack", 0), item.get("starter_pack_name", "")
            ))
            # Get track id from tracks
            row_t = conn.execute("SELECT id FROM tracks WHERE video_id = ?", (video_id,)).fetchone()
            if row_t:
                new_track_id = row_t["id"]
            # Mark or remove filler
            conn.execute("DELETE FROM filler_tracks WHERE id = ?", (item["id"],))
            new_is_filler = False

        elif not source_is_filler and target_type == "music_video":
            # Migrating from tracks to filler_tracks
            conn.execute("""
                INSERT INTO filler_tracks (
                    video_id, title, artist, original_title, duration, is_downloaded,
                    local_path, download_status, is_starter_pack, starter_pack_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title = excluded.title,
                    artist = excluded.artist,
                    is_downloaded = excluded.is_downloaded,
                    local_path = excluded.local_path,
                    download_status = excluded.download_status,
                    is_starter_pack = excluded.is_starter_pack,
                    starter_pack_name = excluded.starter_pack_name
            """, (
                video_id, title, artist, item.get("original_title", f"{artist} - {title}"),
                item.get("duration", 0), 1 if old_file_exists else 0, new_local_path,
                item.get("is_starter_pack", 0), item.get("starter_pack_name", "")
            ))
            row_f = conn.execute("SELECT id FROM filler_tracks WHERE video_id = ?", (video_id,)).fetchone()
            if row_f:
                new_track_id = row_f["id"]
            # Mark track as no longer locally downloaded
            conn.execute("UPDATE tracks SET is_downloaded = 0, local_path = '' WHERE id = ?", (item["id"],))
            new_is_filler = True

        elif not source_is_filler and target_type in ("karaoke", "sing_along"):
            # Migrating between karaoke and sing_along inside tracks
            is_sa = 1 if target_type == "sing_along" else 0
            conn.execute("""
                UPDATE tracks
                SET is_sing_along = ?, local_path = ?
                WHERE id = ?
            """, (is_sa, new_local_path, item["id"]))
            new_track_id = item["id"]
            new_is_filler = False

    conn.close()
    return {
        "success": True,
        "track_id": new_track_id,
        "target_type": target_type,
        "is_filler": new_is_filler,
        "local_path": new_local_path
    }

def get_cleanup_candidates(sort_by: str = "oldest_played", limit: int = 60, tab: str = "all") -> List[Dict[str, Any]]:
    """Returns candidate tracks for pruning storage sorted by longest unplayed or least played, supporting starter pack tab."""
    conn = get_db()
    if sort_by == "least_played":
        order_clause = "ORDER BY play_count ASC, coalesce(last_played, created_at) ASC"
    else: # oldest_played
        order_clause = "ORDER BY coalesce(last_played, '1970-01-01') ASC, play_count ASC"

    starter_filter = "AND is_starter_pack = 1" if tab == "starter_packs" else ""

    # 1. Tracks (Karaoke & Sing-Along)
    query_tracks = f"""
        SELECT id, video_id, title, artist, duration, local_path,
               coalesce(play_count, 0) as play_count, last_played, created_at,
               coalesce(is_sing_along, 0) as is_sing_along,
               coalesce(is_starter_pack, 0) as is_starter_pack,
               coalesce(starter_pack_name, '') as starter_pack_name
        FROM tracks
        WHERE is_downloaded = 1 AND local_path != '' {starter_filter}
        {order_clause}
        LIMIT ?
    """
    rows_tracks = conn.execute(query_tracks, (limit,)).fetchall()

    # 2. Filler Tracks (Music Videos)
    query_filler = f"""
        SELECT id, video_id, title, artist, duration, local_path,
               coalesce(play_count, 0) as play_count, last_played, created_at,
               0 as is_sing_along,
               coalesce(is_starter_pack, 0) as is_starter_pack,
               coalesce(starter_pack_name, '') as starter_pack_name
        FROM filler_tracks
        WHERE is_downloaded = 1 AND local_path != '' {starter_filter}
        {order_clause}
        LIMIT ?
    """
    rows_filler = conn.execute(query_filler, (limit,)).fetchall()
    conn.close()

    results = []
    for r in rows_tracks:
        d = dict(r)
        p = Path(d["local_path"])
        size_bytes = p.stat().st_size if p.exists() else 0
        d["size_bytes"] = size_bytes
        d["size_mb"] = round(size_bytes / (1024 * 1024), 1)
        d["is_filler"] = False
        d["song_type"] = "sing_along" if d.get("is_sing_along") else "karaoke"
        results.append(d)

    for r in rows_filler:
        d = dict(r)
        p = Path(d["local_path"])
        size_bytes = p.stat().st_size if p.exists() else 0
        d["size_bytes"] = size_bytes
        d["size_mb"] = round(size_bytes / (1024 * 1024), 1)
        d["is_filler"] = True
        d["song_type"] = "music_video"
        results.append(d)

    # Sort combined list
    if sort_by == "least_played":
        results.sort(key=lambda x: (x.get("play_count", 0), x.get("last_played") or x.get("created_at") or ""))
    else:
        results.sort(key=lambda x: (x.get("last_played") or "1970-01-01", x.get("play_count", 0)))

    return results[:limit]

def prune_local_track(track_id: int, is_filler: bool = False) -> bool:
    """Removes the downloaded video file on disk while keeping track metadata & history intact."""
    conn = get_db()
    table = "filler_tracks" if is_filler else "tracks"
    track = conn.execute(f"SELECT local_path FROM {table} WHERE id = ?", (track_id,)).fetchone()
    if not track:
        conn.close()
        return False
    local_path = track["local_path"]
    if local_path:
        p = Path(local_path)
        if p.exists():
            try:
                p.unlink()
            except Exception as e:
                print(f"[Storage Prune] Error removing file {p}: {e}")
    with conn:
        conn.execute(f"""
            UPDATE {table}
            SET is_downloaded = 0,
                local_path = '',
                download_status = 'idle'
            WHERE id = ?
        """, (track_id,))
    conn.close()
    return True


# ==============================================================================
# BGM FILLER TRACKS & VIBE ENGINE
# ==============================================================================

def add_filler_track(
    video_id: str,
    title: str,
    artist: str,
    original_title: str,
    duration: int = 0,
    genre: str = "",
    release_year: int = 0,
    is_downloaded: int = 0,
    local_path: str = "",
    media_source: str = "youtube",
    is_starter_pack: int = 0,
    starter_pack_name: str = ""
) -> int:
    conn = get_db()
    if video_id.startswith("local_") and media_source == "youtube":
        media_source = "imported"
    with conn:
        cursor = conn.execute("""
            INSERT INTO filler_tracks (
                video_id, title, artist, original_title, duration,
                genre, release_year, is_downloaded, local_path, download_status, media_source,
                is_starter_pack, starter_pack_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                original_title = excluded.original_title,
                duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE filler_tracks.duration END,
                genre = CASE WHEN excluded.genre != '' THEN excluded.genre ELSE filler_tracks.genre END,
                release_year = CASE WHEN excluded.release_year > 0 THEN excluded.release_year ELSE filler_tracks.release_year END,
                media_source = CASE WHEN filler_tracks.media_source = 'imported' THEN 'imported' ELSE excluded.media_source END,
                is_starter_pack = CASE WHEN excluded.is_starter_pack = 1 THEN 1 ELSE filler_tracks.is_starter_pack END,
                starter_pack_name = CASE WHEN excluded.starter_pack_name != '' THEN excluded.starter_pack_name ELSE filler_tracks.starter_pack_name END
        """, (
            video_id, title, artist, original_title, duration,
            genre, release_year, is_downloaded, local_path,
            "ready" if is_downloaded else "pending", media_source,
            is_starter_pack, starter_pack_name
        ))
        track_id = cursor.lastrowid
        if not track_id:
            row = conn.execute("SELECT id FROM filler_tracks WHERE video_id = ?", (video_id,)).fetchone()
            track_id = row["id"] if row else 0
    conn.close()
    return track_id

def import_local_filler_track(
    file_path: str,
    title: str,
    artist: str,
    duration: int = 0,
    genre: str = "",
    release_year: int = 0
) -> int:
    """Imports an existing local music video or intermission track into the Tiki filler crate."""
    resolved = str(Path(file_path).resolve())
    vid = f"local_filler_{hashlib.sha256(resolved.encode()).hexdigest()[:12]}"
    filler_id = add_filler_track(
        video_id=vid,
        title=title,
        artist=artist,
        original_title=Path(file_path).name,
        genre=genre,
        release_year=release_year,
        duration=duration,
        is_downloaded=1,
        local_path=resolved,
        media_source="imported"
    )
    update_filler_track_status(filler_id, status="ready", local_path=resolved, is_downloaded=1)
    return filler_id

def get_filler_tracks() -> List[Dict[str, Any]]:
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM filler_tracks
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_filler_track_by_id(filler_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute("SELECT * FROM filler_tracks WHERE id = ?", (filler_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_filler_track_by_video_id(video_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute("SELECT * FROM filler_tracks WHERE video_id = ?", (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_filler_track_status(
    filler_id: int,
    status: str,
    progress: int = 0,
    error_message: str = "",
    local_path: str = "",
    is_downloaded: int = 0
):
    conn = get_db()
    updates = ["download_status = ?", "download_progress = ?", "error_message = ?"]
    params: List[Any] = [status, progress, error_message]

    if local_path:
        updates.append("local_path = ?")
        params.append(local_path)
    if is_downloaded is not None:
        updates.append("is_downloaded = ?")
        params.append(is_downloaded)

    params.append(filler_id)
    with conn:
        conn.execute(f"UPDATE filler_tracks SET {', '.join(updates)} WHERE id = ?", params)
    conn.close()

def update_filler_track_metadata(
    filler_id: int,
    genre: str = "",
    release_year: int = 0,
    title: str = "",
    artist: str = ""
):
    conn = get_db()
    updates = []
    params: List[Any] = []
    if genre:
        updates.append("genre = ?")
        params.append(genre)
    if release_year > 0:
        updates.append("release_year = ?")
        params.append(release_year)
    if title:
        updates.append("title = ?")
        params.append(title)
    if artist:
        updates.append("artist = ?")
        params.append(artist)

    if updates:
        params.append(filler_id)
        with conn:
            conn.execute(f"UPDATE filler_tracks SET {', '.join(updates)} WHERE id = ?", params)
    conn.close()

def delete_filler_track(filler_id: int) -> Optional[str]:
    conn = get_db()
    row = conn.execute("SELECT local_path FROM filler_tracks WHERE id = ?", (filler_id,)).fetchone()
    local_path = row["local_path"] if row else None
    with conn:
        conn.execute("DELETE FROM filler_tracks WHERE id = ?", (filler_id,))
    conn.close()
    return local_path

def record_filler_play(filler_id: int, is_sing_along: bool = False):
    conn = get_db()
    with conn:
        if is_sing_along:
            conn.execute("""
                UPDATE tracks
                SET play_count = coalesce(play_count, 0) + 1,
                    last_played = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (filler_id,))
        else:
            conn.execute("""
                UPDATE filler_tracks
                SET play_count = play_count + 1,
                    last_played = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (filler_id,))

        # Record into FIFO play history ring buffer table
        conn.execute("""
            INSERT INTO filler_play_history (filler_id, is_sing_along)
            VALUES (?, ?)
        """, (filler_id, 1 if is_sing_along else 0))
    conn.close()

def get_next_vibe_filler_track(
    last_track_artist: str = "",
    last_track_genre: str = "",
    last_track_year: int = 0,
    mode: Optional[str] = None,
    is_paused: bool = False,
    exclude_track_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """
    Selects the next ready BGM / intermission track according to the host's selected vibe mode:
    - pure_filler: Only music video downloads from the filler crate.
    - sing_along: Only downloaded sing-along tracks with lyrics.
    - intelligent_mashup: Sing-alongs when queue is naturally empty, music videos when show is paused.
    
    Upgraded Vibe Engine:
    1. Rolling 3-Song Multi-History Context: Smoothed era release year + composite genre set + 3-artist diversity.
    2. Adaptive FIFO Blacklist Ring Buffer (Last 6 Plays): Strictly excludes recently played filler tracks.
    3. 90-Minute Sung Song Cooldown: Blocks music videos of tracks live guests sang tonight.
    4. Fair Rotation across crate: Penalizes high play counts.
    """
    conn = get_db()
    strict_byom = get_setting("strict_byom_only", "false") == "true"
    strict_filter = " AND (media_source = 'imported' OR video_id LIKE 'local_%')" if strict_byom else ""

    # Load ready filler tracks
    filler_rows = [dict(r) for r in conn.execute(f"""
        SELECT id, video_id, title, artist, original_title, genre, release_year,
               duration, is_downloaded, local_path, download_status, play_count, last_played,
               0 as is_sing_along, 'filler' as track_type, coalesce(media_source, 'youtube') as media_source
        FROM filler_tracks
        WHERE (download_status = 'ready' OR is_downloaded = 1){strict_filter}
        ORDER BY play_count ASC, coalesce(last_played, '1970-01-01') ASC, id ASC
    """).fetchall()]

    # Load ready sing-along tracks
    sing_along_rows = [dict(r) for r in conn.execute(f"""
        SELECT id, video_id, title, artist, original_title, coalesce(genre, '') as genre, coalesce(release_year, 0) as release_year,
               duration, is_downloaded, local_path, download_status, play_count, last_played,
               1 as is_sing_along, 'sing_along' as track_type, coalesce(media_source, 'youtube') as media_source
        FROM tracks
        WHERE is_sing_along = 1 AND (download_status = 'ready' OR is_downloaded = 1){strict_filter}
        ORDER BY play_count ASC, coalesce(last_played, '1970-01-01') ASC, id ASC
    """).fetchall()]

    # If any tracks have local files on disk, prefer valid files; otherwise keep ready tracks (for mocks/test fixtures)
    existing_filler = [t for t in filler_rows if t.get("local_path") and Path(t["local_path"]).exists()]
    filler_tracks = existing_filler if existing_filler else filler_rows

    existing_sing = [t for t in sing_along_rows if t.get("local_path") and Path(t["local_path"]).exists()]
    sing_along_tracks = existing_sing if existing_sing else sing_along_rows

    # Select candidate pool based on mode and pause state
    if not mode:
        mode = get_setting("intermission_vibe_mode", "sing_along")
    clean_mode = (mode or "sing_along").strip().lower()
    if clean_mode == "pure_filler":
        candidate_pool = filler_tracks if filler_tracks else sing_along_tracks
    elif clean_mode == "sing_along":
        candidate_pool = sing_along_tracks if sing_along_tracks else filler_tracks
    else: # intelligent_mashup
        if is_paused:
            candidate_pool = filler_tracks if filler_tracks else sing_along_tracks
        else:
            candidate_pool = sing_along_tracks if sing_along_tracks else filler_tracks

    if exclude_track_id is not None and len(candidate_pool) > 1:
        filtered = [t for t in candidate_pool if t.get("id") != exclude_track_id]
        if filtered:
            candidate_pool = filtered

    if not candidate_pool:
        conn.close()
        return None

    # 1. FIFO Blacklist Ring Buffer (Last 6 Plays)
    try:
        filler_hist_rows = conn.execute("""
            SELECT filler_id, is_sing_along
            FROM filler_play_history
            ORDER BY id DESC
            LIMIT 7
        """).fetchall()
        recent_filler_keys = [(r["filler_id"], r["is_sing_along"]) for r in filler_hist_rows]
    except Exception as e:
        print(f"[Database] Error checking filler_play_history: {e}")
        recent_filler_keys = []

    # Adaptive ring buffer size: never starve a small crate
    max_blacklist = min(6, max(0, len(candidate_pool) - 2))
    if max_blacklist > 0 and recent_filler_keys:
        active_blacklist = set(recent_filler_keys[:max_blacklist])
        unblacklisted = [t for t in candidate_pool if (t["id"], 1 if t.get("is_sing_along") else 0) not in active_blacklist]
        if unblacklisted:
            candidate_pool = unblacklisted

    # 2. 90-Minute Sung Song Cooldown
    try:
        recent_sung = conn.execute("""
            SELECT LOWER(t.title) as title, LOWER(t.artist) as artist
            FROM history h
            JOIN tracks t ON h.track_id = t.id
            WHERE h.played_at >= datetime('now', '-90 minutes')
        """).fetchall()
        recent_sung_pairs = {(r["artist"].strip(), r["title"].strip()) for r in recent_sung}
    except Exception as e:
        print(f"[Database] Error checking recent sung history: {e}")
        recent_sung_pairs = set()

    # 3. Rolling 3-Song Multi-History Context (Era & Genre Vectors)
    try:
        recent_history_rows = [dict(r) for r in conn.execute("""
            SELECT t.id, t.title, t.artist, coalesce(t.genre, '') as genre, coalesce(t.release_year, 0) as release_year
            FROM history h
            JOIN tracks t ON h.track_id = t.id
            ORDER BY h.id DESC
            LIMIT 3
        """).fetchall()]
    except Exception as e:
        print(f"[Database] Error checking recent performance history: {e}")
        recent_history_rows = []

    conn.close()

    # Build composite rolling window of up to 3 songs
    history_window = []
    if last_track_artist or last_track_genre or last_track_year:
        history_window.append({
            "artist": last_track_artist or "",
            "genre": last_track_genre or "",
            "release_year": int(last_track_year or 0)
        })
    for r in recent_history_rows:
        if len(history_window) >= 3:
            break
        # Avoid duplicate if first history row matches last_track
        if history_window and history_window[0].get("artist", "").lower() == r.get("artist", "").lower() and history_window[0].get("release_year") == r.get("release_year"):
            continue
        history_window.append(r)

    # Multi-artist blacklist from last 3 songs
    recent_artists = {s["artist"].strip().lower() for s in history_window if s.get("artist")}

    # Multi-genre set from last 3 songs (ordered by recency)
    recent_genres = [s["genre"].strip().lower() for s in history_window if s.get("genre")]
    latest_genre = recent_genres[0] if recent_genres else ""

    # Weighted era release year calculation
    years_with_weights = []
    default_weights = [0.5, 0.3, 0.2]
    for idx, s in enumerate(history_window):
        y = s.get("release_year") or 0
        if y > 0:
            w = default_weights[idx] if idx < len(default_weights) else 0.2
            years_with_weights.append((w, y))

    if years_with_weights:
        total_w = sum(w for w, _ in years_with_weights)
        target_era_year = sum(w * y for w, y in years_with_weights) / total_w
    else:
        target_era_year = float(last_track_year) if last_track_year > 0 else 0.0

    # Filter out tracks sung recently tonight unless doing so leaves 0 candidates
    cooled_pool = []
    for t in candidate_pool:
        t_artist = t["artist"].strip().lower()
        t_title = t["title"].strip().lower()
        if (t_artist, t_title) in recent_sung_pairs or (t_title, t_artist) in recent_sung_pairs:
            continue
        cooled_pool.append(t)

    pool = cooled_pool if cooled_pool else candidate_pool

    # 4. Upgraded Vibe Scoring Engine
    def score_track(t: Dict[str, Any]) -> float:
        score = 100.0

        cand_artist = t["artist"].strip().lower()
        cand_genre = (t.get("genre") or "").strip().lower()
        cand_year = t.get("release_year") or 0

        # Multi-artist diversity: heavy penalty if artist appeared anywhere in last 3 songs
        if cand_artist in recent_artists:
            score -= 80.0
        elif recent_artists:
            score += 50.0

        # Multi-genre affinity
        if cand_genre:
            if latest_genre and (cand_genre == latest_genre or cand_genre in latest_genre or latest_genre in cand_genre):
                score += 40.0
            elif any(cand_genre == g or cand_genre in g or g in cand_genre for g in recent_genres):
                score += 25.0

        # Era / Year proximity to smoothed 3-song target year
        if target_era_year > 0 and cand_year > 0:
            year_diff = abs(cand_year - target_era_year)
            if year_diff <= 3:
                score += 35.0
            elif year_diff <= 7:
                score += 20.0
            elif year_diff <= 12:
                score += 10.0
            else:
                score -= min(25.0, (year_diff - 12) * 1.5)

        # Fair crate rotation: penalize higher play counts
        play_cnt = t.get("play_count") or 0
        score -= (play_cnt * 25.0)

        return score

    sorted_candidates = sorted(
        pool,
        key=lambda x: (score_track(x), -(1 if x.get("last_played") else 0)),
        reverse=True
    )

    chosen = dict(sorted_candidates[0])
    chosen["stream_url"] = f"/stream/{chosen['id']}" if chosen["is_sing_along"] else f"/stream/filler/{chosen['id']}"
    return chosen


# ==============================================================================
# Live Singer Performance Recording Operations
# ==============================================================================

def save_recording(
    queue_id: Optional[int],
    singer_id: int,
    track_id: int,
    filename: str,
    file_path: str,
    file_size: int = 0,
    duration_seconds: float = 0.0
) -> int:
    """Records a live singer audio performance into the database."""
    conn = get_db()
    with conn:
        cursor = conn.execute("""
            INSERT INTO recordings (queue_id, singer_id, track_id, filename, file_path, file_size, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (queue_id, singer_id, track_id, filename, file_path, file_size, duration_seconds))
        rec_id = cursor.lastrowid
    conn.close()
    return rec_id

def get_recordings_for_singer(singer_id: int) -> List[Dict[str, Any]]:
    """Returns all performance recordings for a specific singer."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            r.id,
            r.queue_id,
            r.singer_id,
            r.track_id,
            r.filename,
            r.file_path,
            r.file_size,
            r.duration_seconds,
            r.created_at,
            t.title,
            t.artist,
            t.title as song_title,
            t.artist as song_artist
        FROM recordings r
        JOIN tracks t ON r.track_id = t.id
        WHERE r.singer_id = ?
        ORDER BY r.created_at DESC
    """, (singer_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_recordings(limit: int = 100) -> List[Dict[str, Any]]:
    """Returns all live show performance recordings."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            r.id,
            r.queue_id,
            r.singer_id,
            r.track_id,
            r.filename,
            r.file_path,
            r.file_size,
            r.duration_seconds,
            r.created_at,
            s.name as singer_name,
            s.avatar as singer_avatar,
            t.title,
            t.artist,
            t.title as song_title,
            t.artist as song_artist
        FROM recordings r
        JOIN singers s ON r.singer_id = s.id
        JOIN tracks t ON r.track_id = t.id
        ORDER BY r.created_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_recording(recording_id: int) -> Optional[str]:
    """Deletes a recording record and returns its physical file path for unlinking."""
    conn = get_db()
    cursor = conn.execute("SELECT file_path FROM recordings WHERE id = ?", (recording_id,))
    row = cursor.fetchone()
    path = row["file_path"] if row else None
    if row:
        with conn:
            conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
    conn.close()
    return path




