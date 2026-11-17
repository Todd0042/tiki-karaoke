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

from db_library import get_setting

# Session Tracking & Singer Operations
_SESSION_START_TIME = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def set_session_start_time(iso_ts: Optional[str] = None) -> str:
    global _SESSION_START_TIME
    if iso_ts:
        _SESSION_START_TIME = str(iso_ts).replace("T", " ").replace("Z", "")[:19]
    else:
        _SESSION_START_TIME = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return _SESSION_START_TIME

def get_session_start_time() -> str:
    global _SESSION_START_TIME
    return _SESSION_START_TIME

def get_taken_avatars_for_name(
    name: str, 
    exclude_token: str = "", 
    client_ip: str = "", 
    is_host: bool = False
) -> List[str]:
    """Returns list of avatar emojis currently actively claimed by other singers for this specific name tonight."""
    clean_name = name.strip()
    if not clean_name:
        return []
    conn = get_db()
    cursor = conn.execute("""
        SELECT avatar, token, ip_address, last_seen FROM singers 
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND token != ?
    """, (clean_name, exclude_token))
    rows = cursor.fetchall()
    conn.close()

    session_start = get_session_start_time()
    taken = []
    for r in rows:
        av = r["avatar"]
        if not av:
            continue
        last_seen = str(r["last_seen"] or "").replace("T", " ").replace("Z", "")[:19]
        # If is_host is True or client_ip matches, the host/user can reclaim their own avatar:
        if is_host or (client_ip and r["ip_address"] == client_ip):
            continue
        # If not active during the current session, it is available to be reclaimed:
        if last_seen < session_start:
            continue
        taken.append(av)
    return taken

def get_or_create_singer(
    token: str, 
    name: str = "", 
    avatar: str = "🌺", 
    ip_address: str = "", 
    is_host: bool = False
) -> Dict[str, Any]:
    import secrets
    clean_token = token.strip() if token else ""
    if not clean_token or clean_token.lower() in ("undefined", "null"):
        clean_token = f"usr_{secrets.token_hex(6)}"

    clean_name = name.strip() if name else ""
    if clean_name.lower() in ("undefined", "null"):
        clean_name = ""

    conn = get_db()
    with conn:
        cursor = conn.execute("SELECT * FROM singers WHERE token = ?", (clean_token,))
        row = cursor.fetchone()
        clean_avatar = avatar if avatar else "🌺"
        session_start = get_session_start_time()

        if row:
            target_name = clean_name if clean_name else row["name"]
            target_avatar = clean_avatar if avatar else row["avatar"]

            # If changing name or avatar, check if target (name, avatar) exists on another singer
            if target_name.lower() != row["name"].lower() or target_avatar != row["avatar"]:
                other = conn.execute("""
                    SELECT * FROM singers 
                    WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND avatar = ? AND token != ?
                """, (target_name, target_avatar, clean_token)).fetchone()

                if other:
                    other_last_seen = str(other["last_seen"] or "").replace("T", " ").replace("Z", "")[:19]
                    is_active_tonight = (other_last_seen >= session_start) and (not is_host) and (other["ip_address"] != ip_address)
                    if is_active_tonight:
                        raise ValueError(f"'{target_name}' with the {target_avatar} avatar is currently active tonight! Please pick another avatar.")
                    
                    # Reclaim other record! Merge temporary row into other
                    conn.execute("UPDATE queue SET singer_id = ? WHERE singer_id = ?", (other["id"], row["id"]))
                    conn.execute("UPDATE history SET singer_id = ? WHERE singer_id = ?", (other["id"], row["id"]))
                    conn.execute("UPDATE singer_song_log SET singer_id = ? WHERE singer_id = ?", (other["id"], row["id"]))
                    conn.execute("DELETE FROM singers WHERE id = ?", (row["id"],))

                    conn.execute("""
                        UPDATE singers 
                        SET token = ?,
                            ip_address = COALESCE(NULLIF(?, ''), ip_address),
                            last_seen = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (clean_token, ip_address, other["id"]))
                    cursor = conn.execute("SELECT * FROM singers WHERE id = ?", (other["id"],))
                    return dict(cursor.fetchone())

            conn.execute("""
                UPDATE singers 
                SET name = COALESCE(NULLIF(?, ''), name),
                    avatar = COALESCE(NULLIF(?, ''), avatar),
                    ip_address = COALESCE(NULLIF(?, ''), ip_address),
                    last_seen = CURRENT_TIMESTAMP
                WHERE token = ?
            """, (clean_name, clean_avatar, ip_address, clean_token))
            cursor = conn.execute("SELECT * FROM singers WHERE token = ?", (clean_token,))
            row = cursor.fetchone()
            return dict(row)
        else:
            final_name = clean_name if clean_name else "Tiki Singer"
            other = conn.execute("""
                SELECT * FROM singers 
                WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND avatar = ? AND token != ?
            """, (final_name, clean_avatar, clean_token)).fetchone()

            if other:
                other_last_seen = str(other["last_seen"] or "").replace("T", " ").replace("Z", "")[:19]
                is_active_tonight = (other_last_seen >= session_start) and (not is_host) and (other["ip_address"] != ip_address)
                if is_active_tonight:
                    if not clean_name or clean_name.lower().startswith("guest") or clean_name.lower().startswith("tiki singer"):
                        avatars = ["🌺", "🦩", "🍹", "🌴", "🦜", "🥥", "🏄", "🍍"]
                        found = False
                        for av in avatars:
                            chk_av = conn.execute("""
                                SELECT * FROM singers 
                                WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND avatar = ? AND token != ?
                            """, (final_name, av, clean_token)).fetchone()
                            if not chk_av:
                                clean_avatar = av
                                found = True
                                break
                            else:
                                chk_last_seen = str(chk_av["last_seen"] or "").replace("T", " ").replace("Z", "")[:19]
                                if chk_last_seen < session_start or is_host or (ip_address and chk_av["ip_address"] == ip_address):
                                    clean_avatar = av
                                    found = True
                                    other = chk_av
                                    break
                        if not found:
                            suffix = clean_token[-4:].upper() if len(clean_token) >= 4 else secrets.token_hex(2).upper()
                            final_name = f"{final_name} {suffix}"
                            other = None
                    else:
                        raise ValueError(f"'{final_name}' with the {clean_avatar} avatar is currently active tonight! Please pick another avatar.")

            if other:
                # Reclaim & Adopt returning singer profile
                conn.execute("""
                    UPDATE singers 
                    SET token = ?,
                        ip_address = COALESCE(NULLIF(?, ''), ip_address),
                        last_seen = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (clean_token, ip_address, other["id"]))
                cursor = conn.execute("SELECT * FROM singers WHERE id = ?", (other["id"],))
                return dict(cursor.fetchone())

            cursor = conn.execute("""
                INSERT INTO singers (token, name, avatar, ip_address)
                VALUES (?, ?, ?, ?)
            """, (clean_token, final_name, clean_avatar, ip_address))
            cursor = conn.execute("SELECT * FROM singers WHERE id = ?", (cursor.lastrowid,))
            return dict(cursor.fetchone())

def get_singer_by_token(token: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("SELECT * FROM singers WHERE token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def set_singer_status(singer_id: int, status: str) -> bool:
    """Sets a singer's status ('active', 'on_break', 'checked_out') and rebalances queue."""
    clean_status = status.strip().lower()
    if clean_status not in ("active", "on_break", "checked_out"):
        clean_status = "active"
    conn = get_db()
    with conn:
        conn.execute("""
            UPDATE singers 
            SET status = ?, status_updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (clean_status, singer_id))
        rebalance_queue_round_robin(conn)
    conn.close()
    return True

def set_singer_status_by_token(token: str, status: str) -> Optional[Dict[str, Any]]:
    """Sets a singer's status using their secret token (for singer self-pause)."""
    conn = get_db()
    cursor = conn.execute("SELECT id FROM singers WHERE token = ?", (token,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
    singer_id = row["id"]
    conn.close()
    set_singer_status(singer_id, status)
    return get_singer_by_token(token)

def get_singers_with_queue() -> List[Dict[str, Any]]:
    """Returns all singers with their queued songs for host roster management."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            s.id as singer_id,
            s.name as singer_name,
            s.avatar as singer_avatar,
            coalesce(s.status, 'active') as singer_status,
            s.last_seen,
            COUNT(CASE WHEN q.status = 'pending' THEN 1 END) as pending_count,
            COUNT(CASE WHEN q.status = 'playing' THEN 1 END) as playing_count
        FROM singers s
        LEFT JOIN queue q ON s.id = q.singer_id AND q.status IN ('pending', 'playing')
        GROUP BY s.id
        ORDER BY (pending_count + playing_count) DESC, s.last_seen DESC
    """)
    singers_rows = cursor.fetchall()

    result = []
    for s in singers_rows:
        s_dict = dict(s)
        q_cursor = conn.execute("""
            SELECT 
                q.id as queue_id,
                q.status,
                q.pitch_shift,
                q.duet_partner,
                coalesce(q.dedication, '') as dedication,
                q.sort_order,
                t.id as track_id,
                t.title,
                t.artist,
                t.duration,
                coalesce(t.is_sing_along, 0) as is_sing_along
            FROM queue q
            JOIN tracks t ON q.track_id = t.id
            WHERE q.singer_id = ? AND q.status IN ('pending', 'playing')
            ORDER BY 
                CASE q.status WHEN 'playing' THEN 0 ELSE 1 END,
                q.sort_order ASC,
                q.id ASC
        """, (s_dict["singer_id"],))
        s_dict["songs"] = [dict(r) for r in q_cursor.fetchall()]
        result.append(s_dict)

    conn.close()
    return result

def remove_all_songs_by_singer(singer_id: int) -> int:
    """Removes all pending songs for a specific singer from the queue."""
    conn = get_db()
    with conn:
        cursor = conn.execute("""
            DELETE FROM queue 
            WHERE singer_id = ? AND status = 'pending'
        """, (singer_id,))
        count = cursor.rowcount
    conn.close()
    return count

def checkout_singer(singer_id: int) -> bool:
    """Marks a singer as checked out for the current session, freeing their name and avatar."""
    conn = get_db()
    with conn:
        conn.execute("""
            UPDATE singers 
            SET last_seen = '2000-01-01 00:00:00'
            WHERE id = ?
        """, (singer_id,))
    conn.close()
    return True

def purge_all_singers(include_history: bool = True) -> Dict[str, int]:
    """Purges all test/registered singers and associated queue/history records while preserving tracks and settings."""
    conn = get_db()
    with conn:
        q_cnt = conn.execute("DELETE FROM queue").rowcount
        h_cnt = conn.execute("DELETE FROM history").rowcount if include_history else 0
        conn.execute("DELETE FROM singer_song_log")
        s_cnt = conn.execute("DELETE FROM singers").rowcount
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('queue', 'history', 'singers', 'singer_song_log')")
    conn.close()
    return {"singers_deleted": s_cnt, "queue_deleted": q_cnt, "history_deleted": h_cnt}

# Track Operations

def rebalance_queue_round_robin(conn=None):
    """
    Recalculates sort_order for all pending songs using fair round-robin rotation.
    1. Effective round: Each singer gets at most 1 song per round.
       - A singer currently playing on stage has their next pending song placed in Round 2.
       - Other singers have their 1st pending song in Round 1, 2nd pending song in Round 2, etc.
    2. Within the same round:
       - Singers who have sung fewer songs tonight get higher priority.
       - Singers who have waited longest since their last completed song get higher priority.
       - Ties broken by initial queue arrival time (created_at, id).
    """
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True

    try:
        # 1. Identify currently playing singer (if any)
        cursor = conn.execute("SELECT singer_id FROM queue WHERE status = 'playing' LIMIT 1")
        playing_row = cursor.fetchone()
        playing_singer_id = playing_row["singer_id"] if playing_row else None

        # 2. Get history stats per singer (completed song count and last played timestamp)
        cursor = conn.execute("""
            SELECT singer_id, COUNT(*) as completed_count, MAX(played_at) as last_played
            FROM history
            GROUP BY singer_id
        """)
        stats_rows = cursor.fetchall()
        history_stats = {
            r["singer_id"]: {
                "completed_count": r["completed_count"],
                "last_played": r["last_played"] or ""
            } for r in stats_rows
        }

        # 2b. Get singer statuses (active vs on_break)
        cursor = conn.execute("SELECT id, coalesce(status, 'active') as status FROM singers")
        singer_statuses = {r["id"]: r["status"] for r in cursor.fetchall()}

        # 3. Get all pending tracks ordered by created_at ASC, id ASC
        cursor = conn.execute("""
            SELECT id, singer_id, created_at
            FROM queue
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
        """)
        pending_rows = cursor.fetchall()
        if not pending_rows:
            return

        # 4. Group pending songs by singer to compute each song's turn/round for that singer
        singer_pending_counts = {}
        annotated = []
        for r in pending_rows:
            s_id = r["singer_id"]
            count = singer_pending_counts.get(s_id, 0) + 1
            singer_pending_counts[s_id] = count

            s_status = singer_statuses.get(s_id, "active")
            is_on_break = 1 if s_status == "on_break" else 0

            # If this singer is currently playing on stage, their turn in Round 1 is live,
            # so their 1st pending song belongs in Round 2.
            # Otherwise, their 1st pending song belongs in Round 1.
            is_currently_playing = (s_id == playing_singer_id)
            effective_round = count + 1 if is_currently_playing else count

            h = history_stats.get(s_id, {"completed_count": 0, "last_played": ""})
            completed_count = h["completed_count"]
            last_played = h["last_played"]

            annotated.append({
                "id": r["id"],
                "singer_id": s_id,
                "is_on_break": is_on_break,
                "effective_round": effective_round,
                "completed_count": completed_count,
                "last_played": last_played,
                "created_at": r["created_at"]
            })

        # Track the simulated last turn position of each singer.
        # Position 0 represents the stage.
        last_turn_pos = {}
        if playing_singer_id:
            last_turn_pos[playing_singer_id] = 0

        # For singers not on stage, initialize last_turn_pos based on completed count and last_played:
        # Singers with fewer completed songs start with a smaller (earlier) position.
        # Singers who have never sung tonight start with lowest position (-10000).
        for item in annotated:
            s_id = item["singer_id"]
            if s_id not in last_turn_pos:
                cc = item["completed_count"]
                if cc == 0:
                    last_turn_pos[s_id] = -10000
                else:
                    last_turn_pos[s_id] = -5000 + cc

        # Process rounds in strict order:
        # First active singers (is_on_break = 0) by round 1, 2, 3...
        # Then singers on break (is_on_break = 1) by round 1, 2, 3...
        ordered_items = []
        current_pos = 1

        for on_break_val in (0, 1):
            subset = [x for x in annotated if x["is_on_break"] == on_break_val]
            if not subset:
                continue
            round_nums = sorted(list(set(x["effective_round"] for x in subset)))
            for r in round_nums:
                round_items = [x for x in subset if x["effective_round"] == r]
                # Sort singers within this round:
                # 1. Who has waited longest since their last turn (smallest last_turn_pos)
                # 2. Fewer completed songs tonight
                # 3. Last played timestamp ASC ("" sorts first)
                # 4. Initial queue creation time ASC
                # 5. ID ASC
                round_items.sort(key=lambda x: (
                    last_turn_pos.get(x["singer_id"], -9999),
                    x["completed_count"],
                    x["last_played"],
                    x["created_at"],
                    x["id"]
                ))
                for item in round_items:
                    ordered_items.append(item)
                    last_turn_pos[item["singer_id"]] = current_pos
                    current_pos += 1

        # Update sort_order in database
        for idx, item in enumerate(ordered_items):
            conn.execute("UPDATE queue SET sort_order = ? WHERE id = ?", (float(idx + 1), item["id"]))
        if close_conn:
            conn.commit()

    finally:
        if close_conn:
            conn.close()

def enqueue_song(track_id: int, singer_id: int, duet_partner: str = "", pitch_shift: int = 0, dedication: str = "") -> int:
    conn = get_db()
    with conn:
        cursor = conn.execute("""
            INSERT INTO queue (track_id, singer_id, duet_partner, pitch_shift, dedication, sort_order)
            VALUES (?, ?, ?, ?, ?, 999999.0)
        """, (track_id, singer_id, duet_partner, pitch_shift, (dedication or "").strip()[:60]))
        queue_id = cursor.lastrowid

        # Record in singer songbook log for personal queue tracking across sessions
        conn.execute("""
            INSERT INTO singer_song_log (singer_id, track_id, pitch_shift, duet_partner)
            VALUES (?, ?, ?, ?)
        """, (singer_id, track_id, pitch_shift, duet_partner))

        # If duet partner is also a registered singer, log for their profile too
        if duet_partner:
            partner_row = conn.execute(
                "SELECT id FROM singers WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1",
                (duet_partner,)
            ).fetchone()
            if partner_row:
                conn.execute("""
                    INSERT INTO singer_song_log (singer_id, track_id, pitch_shift, duet_partner)
                    VALUES (?, ?, ?, '')
                """, (partner_row["id"], track_id, pitch_shift))

        rebalance_queue_round_robin(conn)
    conn.close()
    return queue_id

def get_pending_queue_count_for_singer(singer_id: int) -> int:
    conn = get_db()
    cursor = conn.execute(
        "SELECT COUNT(*) as count FROM queue WHERE singer_id = ? AND status = 'pending'",
        (singer_id,)
    )
    row = cursor.fetchone()
    count_queue = row["count"] if row else 0
    cursor = conn.execute(
        "SELECT COUNT(*) as count FROM pending_downloads WHERE singer_id = ? AND status IN ('pending', 'downloading')",
        (singer_id,)
    )
    row = cursor.fetchone()
    count_downloads = row["count"] if row else 0
    conn.close()
    return count_queue + count_downloads

def get_queue() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            q.id as queue_id,
            q.status,
            q.pitch_shift,
            q.duet_partner,
            coalesce(q.dedication, '') as dedication,
            q.sort_order,
            q.created_at,
            t.id as track_id,
            t.video_id,
            t.title,
            t.artist,
            t.original_title,
            t.duration,
            t.is_downloaded,
            t.local_path,
            t.download_status,
            coalesce(t.normalized_lufs, -16.0) as normalized_lufs,
            coalesce(t.is_sing_along, 0) as is_sing_along,
            coalesce(t.media_type, 'video') as media_type,
            coalesce(t.cdg_path, '') as cdg_path,
            s.id as singer_id,
            s.name as singer_name,
            s.avatar as singer_avatar,
            coalesce(s.status, 'active') as singer_status
        FROM queue q
        JOIN tracks t ON q.track_id = t.id
        JOIN singers s ON q.singer_id = s.id
        WHERE q.status IN ('playing', 'pending')
        ORDER BY 
            CASE q.status WHEN 'playing' THEN 0 ELSE 1 END,
            q.sort_order ASC,
            q.id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_pending_download(
    track_id: int = 0,
    singer_id: int = 0,
    duet_partner: str = "",
    pitch_shift: int = 0,
    dedication: str = "",
    quality_profile: str = "default",
    filler_id: int = 0,
    priority: int = 10,
    auto_promote: int = 1,
    is_starter_pack: int = 0,
    starter_pack_name: str = ""
) -> int:
    conn = get_db()
    with conn:
        cursor = conn.execute("""
            INSERT INTO pending_downloads (
                track_id, filler_id, singer_id, duet_partner, pitch_shift, dedication, quality_profile, status,
                priority, auto_promote, is_starter_pack, starter_pack_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'downloading', ?, ?, ?, ?)
        """, (
            track_id if track_id else None,
            filler_id if filler_id else None,
            singer_id if singer_id else None,
            duet_partner,
            pitch_shift,
            (dedication or "").strip()[:60],
            quality_profile,
            priority,
            auto_promote,
            is_starter_pack,
            starter_pack_name
        ))
        pending_id = cursor.lastrowid
    conn.close()
    return pending_id

def get_pending_downloads() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            p.id,
            p.track_id,
            coalesce(p.filler_id, 0) as filler_id,
            p.singer_id,
            p.duet_partner,
            p.pitch_shift,
            coalesce(p.dedication, '') as dedication,
            p.progress,
            p.status,
            coalesce(p.error_message, '') as error_message,
            p.retry_count,
            p.quality_profile,
            coalesce(p.priority, 10) as priority,
            coalesce(p.auto_promote, 1) as auto_promote,
            coalesce(p.is_starter_pack, 0) as is_starter_pack,
            coalesce(p.starter_pack_name, '') as starter_pack_name,
            p.created_at,
            coalesce(t.video_id, f.video_id, '') as video_id,
            coalesce(t.title, f.title, '') as title,
            coalesce(t.artist, f.artist, '') as artist,
            coalesce(t.original_title, f.original_title, '') as original_title,
            coalesce(t.duration, f.duration, 0) as duration,
            coalesce(t.is_downloaded, f.is_downloaded, 0) as is_downloaded,
            coalesce(t.download_status, f.download_status, 'pending') as download_status,
            coalesce(t.is_sing_along, 0) as is_sing_along,
            coalesce(s.name, CASE WHEN p.is_starter_pack = 1 THEN '📦 Starter Pack' ELSE 'Host DJ' END) as singer_name,
            coalesce(s.avatar, '🌴') as singer_avatar
        FROM pending_downloads p
        LEFT JOIN tracks t ON p.track_id = t.id
        LEFT JOIN filler_tracks f ON p.filler_id = f.id
        LEFT JOIN singers s ON p.singer_id = s.id
        ORDER BY p.priority ASC, p.id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_pending_download_by_id(pending_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            p.id,
            p.track_id,
            coalesce(p.filler_id, 0) as filler_id,
            p.singer_id,
            p.duet_partner,
            p.pitch_shift,
            coalesce(p.dedication, '') as dedication,
            p.progress,
            p.status,
            coalesce(p.error_message, '') as error_message,
            p.retry_count,
            p.quality_profile,
            coalesce(p.priority, 10) as priority,
            coalesce(p.auto_promote, 1) as auto_promote,
            coalesce(p.is_starter_pack, 0) as is_starter_pack,
            coalesce(p.starter_pack_name, '') as starter_pack_name,
            p.created_at,
            coalesce(t.video_id, f.video_id, '') as video_id,
            coalesce(t.title, f.title, '') as title,
            coalesce(t.artist, f.artist, '') as artist,
            coalesce(t.original_title, f.original_title, '') as original_title,
            coalesce(t.duration, f.duration, 0) as duration,
            coalesce(t.is_downloaded, f.is_downloaded, 0) as is_downloaded,
            coalesce(t.download_status, f.download_status, 'pending') as download_status,
            coalesce(t.is_sing_along, 0) as is_sing_along,
            coalesce(s.name, CASE WHEN p.is_starter_pack = 1 THEN '📦 Starter Pack' ELSE 'Host DJ' END) as singer_name,
            coalesce(s.avatar, '🌴') as singer_avatar
        FROM pending_downloads p
        LEFT JOIN tracks t ON p.track_id = t.id
        LEFT JOIN filler_tracks f ON p.filler_id = f.id
        LEFT JOIN singers s ON p.singer_id = s.id
        WHERE p.id = ?
        LIMIT 1
    """, (pending_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_pending_download_by_track_id(track_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            p.id,
            p.track_id,
            coalesce(p.filler_id, 0) as filler_id,
            p.singer_id,
            p.duet_partner,
            p.pitch_shift,
            coalesce(p.dedication, '') as dedication,
            p.progress,
            p.status,
            coalesce(p.error_message, '') as error_message,
            p.retry_count,
            p.quality_profile,
            coalesce(p.priority, 10) as priority,
            coalesce(p.auto_promote, 1) as auto_promote,
            coalesce(p.is_starter_pack, 0) as is_starter_pack,
            coalesce(p.starter_pack_name, '') as starter_pack_name,
            p.created_at,
            coalesce(t.video_id, f.video_id, '') as video_id,
            coalesce(t.title, f.title, '') as title,
            coalesce(t.artist, f.artist, '') as artist,
            coalesce(t.original_title, f.original_title, '') as original_title,
            coalesce(t.duration, f.duration, 0) as duration,
            coalesce(t.is_downloaded, f.is_downloaded, 0) as is_downloaded,
            coalesce(t.download_status, f.download_status, 'pending') as download_status,
            coalesce(t.is_sing_along, 0) as is_sing_along,
            coalesce(s.name, CASE WHEN p.is_starter_pack = 1 THEN '📦 Starter Pack' ELSE 'Host DJ' END) as singer_name,
            coalesce(s.avatar, '🌴') as singer_avatar
        FROM pending_downloads p
        LEFT JOIN tracks t ON p.track_id = t.id
        LEFT JOIN filler_tracks f ON p.filler_id = f.id
        LEFT JOIN singers s ON p.singer_id = s.id
        WHERE p.track_id = ?
        ORDER BY p.id DESC
        LIMIT 1
    """, (track_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_download_by_filler_id(filler_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            p.id,
            p.track_id,
            coalesce(p.filler_id, 0) as filler_id,
            p.singer_id,
            p.duet_partner,
            p.pitch_shift,
            coalesce(p.dedication, '') as dedication,
            p.progress,
            p.status,
            coalesce(p.error_message, '') as error_message,
            p.retry_count,
            p.quality_profile,
            coalesce(p.priority, 10) as priority,
            coalesce(p.auto_promote, 1) as auto_promote,
            coalesce(p.is_starter_pack, 0) as is_starter_pack,
            coalesce(p.starter_pack_name, '') as starter_pack_name,
            p.created_at,
            coalesce(f.video_id, '') as video_id,
            coalesce(f.title, '') as title,
            coalesce(f.artist, '') as artist,
            coalesce(f.original_title, '') as original_title,
            coalesce(f.duration, 0) as duration,
            coalesce(f.is_downloaded, 0) as is_downloaded,
            coalesce(f.download_status, 'pending') as download_status,
            0 as is_sing_along,
            coalesce(s.name, CASE WHEN p.is_starter_pack = 1 THEN '📦 Starter Pack' ELSE 'Host DJ' END) as singer_name,
            coalesce(s.avatar, '🌴') as singer_avatar
        FROM pending_downloads p
        LEFT JOIN filler_tracks f ON p.filler_id = f.id
        LEFT JOIN singers s ON p.singer_id = s.id
        WHERE p.filler_id = ?
        ORDER BY p.id DESC
        LIMIT 1
    """, (filler_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def find_existing_downloaded_track(artist: str, title: str, mode: str = "karaoke") -> Optional[Dict[str, Any]]:
    """
    Checks if a track with matching (artist AND title) is already downloaded in SQLite.
    Title alone does not constitute duplication; both must match.
    Verifies that the physical MP4 file still exists on disk.
    """
    def norm(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', (s or '').lower())

    norm_art = norm(artist)
    norm_tit = norm(title)
    if not norm_art or not norm_tit:
        return None

    conn = get_db()
    mode = (mode or "karaoke").lower()
    match = None

    if mode == "music_video":
        rows = conn.execute("SELECT id, title, artist, video_id, local_path, is_downloaded FROM filler_tracks WHERE is_downloaded = 1").fetchall()
        for r in rows:
            if norm(r["artist"]) == norm_art and norm(r["title"]) == norm_tit:
                cand = dict(r)
                if cand.get("local_path") and Path(cand["local_path"]).exists():
                    match = cand
                    break
    else:
        is_sa = 1 if mode == "sing_along" else 0
        rows = conn.execute("SELECT id, title, artist, video_id, local_path, is_downloaded, is_sing_along FROM tracks WHERE is_downloaded = 1 AND coalesce(is_sing_along, 0) = ?", (is_sa,)).fetchall()
        for r in rows:
            if norm(r["artist"]) == norm_art and norm(r["title"]) == norm_tit:
                cand = dict(r)
                if cand.get("local_path") and Path(cand["local_path"]).exists():
                    match = cand
                    break

    conn.close()
    return match


def update_pending_download_progress(
    pending_id: int,
    progress: float,
    status: Optional[str] = None,
    error_message: Optional[str] = None
):
    conn = get_db()
    with conn:
        updates = ["progress = ?"]
        params = [round(float(progress), 1)]
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        params.append(pending_id)
        conn.execute(f"UPDATE pending_downloads SET {', '.join(updates)} WHERE id = ?", params)
    conn.close()

def increment_pending_download_retry(pending_id: int, quality_profile: Optional[str] = None) -> int:
    conn = get_db()
    with conn:
        if quality_profile:
            conn.execute("""
                UPDATE pending_downloads
                SET retry_count = retry_count + 1,
                    quality_profile = ?,
                    status = 'downloading',
                    progress = 0.0,
                    error_message = ''
                WHERE id = ?
            """, (quality_profile, pending_id))
        else:
            conn.execute("""
                UPDATE pending_downloads
                SET retry_count = retry_count + 1,
                    status = 'downloading',
                    progress = 0.0,
                    error_message = ''
                WHERE id = ?
            """, (pending_id,))
        row = conn.execute("SELECT retry_count FROM pending_downloads WHERE id = ?", (pending_id,)).fetchone()
        count = row["retry_count"] if row else 0
    conn.close()
    return count

def remove_pending_download(pending_id: int) -> bool:
    conn = get_db()
    with conn:
        cursor = conn.execute("DELETE FROM pending_downloads WHERE id = ?", (pending_id,))
        deleted = cursor.rowcount > 0
    conn.close()
    return deleted

def promote_pending_download_to_queue(pending_id: int) -> Optional[int]:
    """Promotes a downloaded track from pending_downloads into the active queue with fair round-robin."""
    conn = get_db()
    with conn:
        cursor = conn.execute("SELECT * FROM pending_downloads WHERE id = ?", (pending_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        p = dict(row)
        conn.execute("DELETE FROM pending_downloads WHERE id = ?", (pending_id,))
    conn.close()

    # If auto_promote is turned off (e.g. background starter pack caching), do not add to active queue
    if p.get("auto_promote") == 0:
        return None

    # If this was solely a filler music video, do not add to singer queue
    if (p.get("filler_id") or 0) > 0 and not (p.get("track_id") or 0):
        return None

    if not p.get("track_id") or not p.get("singer_id"):
        return None

    # Now enqueue the song into active queue (which triggers round-robin rebalancing)
    queue_id = enqueue_song(
        track_id=p["track_id"],
        singer_id=p["singer_id"],
        duet_partner=p["duet_partner"] or "",
        pitch_shift=p["pitch_shift"] or 0,
        dedication=p["dedication"] or ""
    )
    return queue_id


def clear_queue_on_startup():
    """Clears any lingering queue entries when the server starts so queue starts fresh."""
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM queue")
    conn.close()

def get_current_playing() -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            q.id as queue_id,
            q.pitch_shift,
            q.duet_partner,
            coalesce(q.dedication, '') as dedication,
            q.played_at,
            t.id as track_id,
            t.video_id,
            t.title,
            t.artist,
            t.duration,
            t.is_downloaded,
            t.local_path,
            coalesce(t.normalized_lufs, -16.0) as normalized_lufs,
            coalesce(t.is_sing_along, 0) as is_sing_along,
            coalesce(t.media_type, 'video') as media_type,
            coalesce(t.cdg_path, '') as cdg_path,
            s.id as singer_id,
            s.name as singer_name,
            s.avatar as singer_avatar,
            coalesce(s.status, 'active') as singer_status
        FROM queue q
        JOIN tracks t ON q.track_id = t.id
        JOIN singers s ON q.singer_id = s.id
        WHERE q.status = 'playing'
        LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    if data.get("played_at"):
        try:
            ts_str = str(data["played_at"]).replace("Z", "+00:00")
            if "T" in ts_str:
                played_dt = datetime.datetime.fromisoformat(ts_str)
            else:
                played_dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            data["elapsed_seconds"] = max(0.0, (now_dt - played_dt).total_seconds())
        except Exception:
            data["elapsed_seconds"] = 0.0
    else:
        data["elapsed_seconds"] = 0.0
    return data

def set_queue_status(queue_id: int, status: str):
    conn = get_db()
    with conn:
        if status == 'playing':
            # Complete currently playing track and record history
            playing_rows = conn.execute("SELECT id, track_id, singer_id, duet_partner, pitch_shift FROM queue WHERE status = 'playing'").fetchall()
            for p in playing_rows:
                conn.execute("""
                    INSERT INTO history (track_id, singer_id, duet_partner, pitch_shift)
                    VALUES (?, ?, ?, ?)
                """, (p["track_id"], p["singer_id"], p["duet_partner"], p["pitch_shift"]))
                conn.execute("""
                    UPDATE tracks 
                    SET play_count = coalesce(play_count, 0) + 1,
                        last_played = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (p["track_id"],))
            now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute("UPDATE queue SET status = 'completed' WHERE status = 'playing'")
            conn.execute("UPDATE queue SET status = 'playing', played_at = ? WHERE id = ?", (now_utc, queue_id))
        elif status == 'completed':
            cursor = conn.execute("SELECT track_id, singer_id, duet_partner, pitch_shift FROM queue WHERE id = ?", (queue_id,))
            q_row = cursor.fetchone()
            if q_row:
                conn.execute("""
                    INSERT INTO history (track_id, singer_id, duet_partner, pitch_shift)
                    VALUES (?, ?, ?, ?)
                """, (q_row["track_id"], q_row["singer_id"], q_row["duet_partner"], q_row["pitch_shift"]))
                conn.execute("""
                    UPDATE tracks 
                    SET play_count = coalesce(play_count, 0) + 1,
                        last_played = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (q_row["track_id"],))
            conn.execute("UPDATE queue SET status = 'completed' WHERE id = ?", (queue_id,))
        elif status == 'skipped':
            conn.execute("UPDATE queue SET status = 'skipped' WHERE id = ?", (queue_id,))
        elif status == 'pending':
            conn.execute("UPDATE queue SET status = 'pending', played_at = NULL WHERE id = ?", (queue_id,))
        else:
            conn.execute("UPDATE queue SET status = ? WHERE id = ?", (status, queue_id))
        rebalance_queue_round_robin(conn)
    conn.close()

def remove_from_queue(queue_id: int, singer_id: Optional[int] = None) -> bool:
    conn = get_db()
    with conn:
        if singer_id:
            cursor = conn.execute("DELETE FROM queue WHERE id = ? AND singer_id = ? AND status = 'pending'", (queue_id, singer_id))
        else:
            cursor = conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))
        deleted = cursor.rowcount > 0
        if deleted:
            rebalance_queue_round_robin(conn)
    conn.close()
    return deleted

def normalize_song_string(s: str) -> str:
    if not s:
        return ""
    import re
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()

def check_song_queue_duplicate(
    singer_id: int,
    track_id: Optional[int] = None,
    video_id: Optional[str] = None,
    title: Optional[str] = None,
    artist: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Checks if a song is already active in queue (playing or pending) or sung earlier tonight.
    Returns details about the duplicate or None if clear to queue.
    """
    conn = get_db()
    norm_title = normalize_song_string(title or "")
    norm_artist = normalize_song_string(artist or "")

    # 1. Check active queue (playing or pending)
    cursor = conn.execute("""
        SELECT 
            q.id as queue_id,
            q.status,
            q.singer_id,
            q.duet_partner,
            q.sort_order,
            s.name as singer_name,
            s.avatar as singer_avatar,
            t.id as track_id,
            t.video_id,
            t.title,
            t.artist,
            coalesce(t.is_sing_along, 0) as is_sing_along
        FROM queue q
        JOIN tracks t ON q.track_id = t.id
        JOIN singers s ON q.singer_id = s.id
        WHERE q.status IN ('playing', 'pending')
        ORDER BY 
            CASE q.status WHEN 'playing' THEN 0 ELSE 1 END,
            q.sort_order ASC,
            q.id ASC
    """)
    active_rows = [dict(r) for r in cursor.fetchall()]

    # Calculate pending queue positions (1st in line, 2nd in line...)
    pending_count = 0
    for r in active_rows:
        if r["status"] == "pending":
            pending_count += 1
            r["position_in_line"] = pending_count
        else:
            r["position_in_line"] = 0

    for item in active_rows:
        is_match = False
        if track_id and item["track_id"] == track_id:
            is_match = True
        elif video_id and item["video_id"] == video_id:
            is_match = True
        elif norm_title and norm_artist:
            item_title = normalize_song_string(item["title"])
            item_artist = normalize_song_string(item["artist"])
            if norm_title == item_title and norm_artist == item_artist:
                is_match = True

        if is_match:
            conn.close()
            is_self = (item["singer_id"] == singer_id)
            if item["status"] == "playing":
                match_type = "playing"
            elif is_self:
                match_type = "self"
            else:
                match_type = "other_singer"

            return {
                "duplicate_found": True,
                "match_type": match_type,
                "status": item["status"],
                "existing_queue_id": item["queue_id"],
                "existing_singer_id": item["singer_id"],
                "existing_singer_name": item["singer_name"],
                "existing_singer_avatar": item["singer_avatar"],
                "existing_duet_partner": item.get("duet_partner") or "",
                "is_self": is_self,
                "song_title": f"{item['artist']} - {item['title']}",
                "title": item["title"],
                "artist": item["artist"],
                "position_in_line": item["position_in_line"]
            }

    # 1.5 Check pending downloads
    cursor = conn.execute("""
        SELECT 
            p.id as pending_id,
            p.singer_id,
            p.duet_partner,
            s.name as singer_name,
            s.avatar as singer_avatar,
            t.id as track_id,
            t.video_id,
            t.title,
            t.artist
        FROM pending_downloads p
        JOIN tracks t ON p.track_id = t.id
        JOIN singers s ON p.singer_id = s.id
        WHERE p.status IN ('pending', 'downloading')
    """)
    pending_dl_rows = [dict(r) for r in cursor.fetchall()]
    for item in pending_dl_rows:
        is_match = False
        if track_id and item["track_id"] == track_id:
            is_match = True
        elif video_id and item["video_id"] == video_id:
            is_match = True
        elif norm_title and norm_artist:
            item_title = normalize_song_string(item["title"])
            item_artist = normalize_song_string(item["artist"])
            if norm_title == item_title and norm_artist == item_artist:
                is_match = True

        if is_match:
            conn.close()
            is_self = (item["singer_id"] == singer_id)
            match_type = "self" if is_self else "other_singer"
            return {
                "duplicate_found": True,
                "match_type": match_type,
                "status": "downloading",
                "existing_queue_id": item["pending_id"],
                "existing_singer_id": item["singer_id"],
                "existing_singer_name": item["singer_name"],
                "existing_singer_avatar": item["singer_avatar"],
                "existing_duet_partner": item.get("duet_partner") or "",
                "is_self": is_self,
                "song_title": f"{item['artist']} - {item['title']}",
                "title": item["title"],
                "artist": item["artist"],
                "position_in_line": "Downloading..."
            }

    # 2. Check history (sung within the last 6 hours)
    cursor = conn.execute("""
        SELECT 
            h.id as history_id,
            h.played_at,
            h.singer_id,
            s.name as singer_name,
            s.avatar as singer_avatar,
            t.id as track_id,
            t.video_id,
            t.title,
            t.artist
        FROM history h
        JOIN tracks t ON h.track_id = t.id
        JOIN singers s ON h.singer_id = s.id
        WHERE h.played_at >= datetime('now', '-6 hours')
        ORDER BY h.played_at DESC
    """)
    history_rows = [dict(r) for r in cursor.fetchall()]

    for item in history_rows:
        is_match = False
        if track_id and item["track_id"] == track_id:
            is_match = True
        elif video_id and item["video_id"] == video_id:
            is_match = True
        elif norm_title and norm_artist:
            item_title = normalize_song_string(item["title"])
            item_artist = normalize_song_string(item["artist"])
            if norm_title == item_title and norm_artist == item_artist:
                is_match = True

        if is_match:
            conn.close()
            is_self = (item["singer_id"] == singer_id)
            return {
                "duplicate_found": True,
                "match_type": "played_tonight",
                "status": "completed",
                "existing_queue_id": None,
                "existing_singer_id": item["singer_id"],
                "existing_singer_name": item["singer_name"],
                "existing_singer_avatar": item["singer_avatar"],
                "existing_duet_partner": "",
                "is_self": is_self,
                "song_title": f"{item['artist']} - {item['title']}",
                "title": item["title"],
                "artist": item["artist"],
                "played_at": item["played_at"],
                "position_in_line": None
            }

    conn.close()
    return None

def add_duet_partner(queue_id: int, new_partner_name: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    with conn:
        cursor = conn.execute("SELECT duet_partner FROM queue WHERE id = ?", (queue_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        current_duet = (row["duet_partner"] or "").strip()
        clean_new = new_partner_name.strip()
        if not current_duet:
            updated_duet = clean_new
        elif clean_new.lower() in [p.strip().lower() for p in current_duet.replace('&', ',').split(',')]:
            updated_duet = current_duet
        else:
            updated_duet = f"{current_duet} & {clean_new}"
        
        conn.execute("UPDATE queue SET duet_partner = ? WHERE id = ?", (updated_duet, queue_id))

        # Log for the joining partner in singer_song_log
        q_item = conn.execute("SELECT track_id, pitch_shift FROM queue WHERE id = ?", (queue_id,)).fetchone()
        if q_item:
            partner_singer = conn.execute(
                "SELECT id FROM singers WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1",
                (clean_new,)
            ).fetchone()
            if partner_singer:
                conn.execute("""
                    INSERT INTO singer_song_log (singer_id, track_id, pitch_shift, duet_partner)
                    VALUES (?, ?, ?, '')
                """, (partner_singer["id"], q_item["track_id"], q_item["pitch_shift"]))
    conn.close()
    return {"queue_id": queue_id, "duet_partner": updated_duet}

def remove_duet_partner(queue_id: int, singer_name: str, singer_token: str = "") -> Optional[Dict[str, Any]]:
    conn = get_db()
    with conn:
        cursor = conn.execute("""
            SELECT q.id, q.singer_id, q.duet_partner, q.status, s.name as primary_name, s.token as primary_token
            FROM queue q
            JOIN singers s ON q.singer_id = s.id
            WHERE q.id = ?
        """, (queue_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None

        primary_name = (row["primary_name"] or "").strip()
        duet_raw = (row["duet_partner"] or "").strip()
        partners = [p.strip() for p in duet_raw.replace("&", ",").split(",") if p.strip()]

        clean_name = singer_name.strip()
        is_primary = False
        if singer_token and row["primary_token"] and singer_token == row["primary_token"]:
            is_primary = True
        elif clean_name and primary_name and clean_name.lower() == primary_name.lower():
            is_primary = True

        res = None
        if is_primary:
            if not partners:
                conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))
                rebalance_queue_round_robin(conn)
                res = {"queue_id": queue_id, "deleted": True}
            else:
                new_primary_name = partners[0]
                remaining_partners = partners[1:]
                new_duet = " & ".join(remaining_partners)

                s_chk = conn.execute("""
                    SELECT id FROM singers 
                    WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
                    ORDER BY id ASC LIMIT 1
                """, (new_primary_name,)).fetchone()
                if s_chk:
                    new_singer_id = s_chk["id"]
                else:
                    new_tok = f"usr_{secrets.token_hex(6)}"
                    c2 = conn.execute("INSERT INTO singers (token, name, avatar) VALUES (?, ?, '🎤')", (new_tok, new_primary_name))
                    new_singer_id = c2.lastrowid

                conn.execute("UPDATE queue SET singer_id = ?, duet_partner = ? WHERE id = ?", (new_singer_id, new_duet, queue_id))
                rebalance_queue_round_robin(conn)
                res = {
                    "queue_id": queue_id,
                    "primary_name": new_primary_name,
                    "duet_partner": new_duet,
                    "deleted": False
                }
        else:
            new_partners = [p for p in partners if p.lower() != clean_name.lower()]
            new_duet = " & ".join(new_partners)
            conn.execute("UPDATE queue SET duet_partner = ? WHERE id = ?", (new_duet, queue_id))
            res = {
                "queue_id": queue_id,
                "primary_name": primary_name,
                "duet_partner": new_duet,
                "deleted": False
            }

    conn.close()
    return res

# History & Stats
