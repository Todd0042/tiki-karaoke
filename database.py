import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from config import get_db_path, get_data_dir, ensure_storage_dirs, DB_PATH

def get_db():
    ensure_storage_dirs()
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS singers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            avatar TEXT DEFAULT '🌺',
            ip_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            original_title TEXT NOT NULL,
            channel TEXT DEFAULT '',
            duration INTEGER DEFAULT 0,
            is_downloaded INTEGER DEFAULT 0,
            local_path TEXT DEFAULT '',
            normalized_lufs REAL DEFAULT -16.0,
            download_status TEXT DEFAULT 'idle', -- 'idle', 'downloading', 'ready', 'failed'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            play_count INTEGER DEFAULT 0,
            last_played TIMESTAMP
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
            title,
            artist,
            original_title,
            channel,
            content='tracks',
            content_rowid='id'
        );

        -- Triggers to keep FTS index synchronized
        CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
            INSERT INTO tracks_fts(rowid, title, artist, original_title, channel)
            VALUES (new.id, new.title, new.artist, new.original_title, new.channel);
        END;

        CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
            INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, original_title, channel)
            VALUES('delete', old.id, old.title, old.artist, old.original_title, old.channel);
        END;

        CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
            INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, original_title, channel)
            VALUES('delete', old.id, old.title, old.artist, old.original_title, old.channel);
            INSERT INTO tracks_fts(rowid, title, artist, original_title, channel)
            VALUES (new.id, new.title, new.artist, new.original_title, new.channel);
        END;

        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL REFERENCES tracks(id),
            singer_id INTEGER NOT NULL REFERENCES singers(id),
            duet_partner TEXT DEFAULT '',
            pitch_shift INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending', -- 'pending', 'playing', 'completed', 'skipped'
            sort_order REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            played_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL REFERENCES tracks(id),
            singer_id INTEGER NOT NULL REFERENCES singers(id),
            duet_partner TEXT DEFAULT '',
            pitch_shift INTEGER DEFAULT 0,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            youtube_playlist_id TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        -- Default settings
        INSERT OR IGNORE INTO settings (key, value) VALUES ('tts_enabled', 'false');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('intermission_seconds', '20');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bgm_enabled', 'true');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bgm_volume', '0.25');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bar_name', 'The Tiki Hut Karaoke Lounge');
        """)

        # Ensure play_count and last_played exist on existing databases
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()]
        if "play_count" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN play_count INTEGER DEFAULT 0")
        if "last_played" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN last_played TIMESTAMP")

        # Backfill play counts and last_played from history if present
        conn.execute("""
            UPDATE tracks
            SET play_count = (SELECT COUNT(*) FROM history WHERE history.track_id = tracks.id),
                last_played = (SELECT MAX(played_at) FROM history WHERE history.track_id = tracks.id)
            WHERE EXISTS (SELECT 1 FROM history WHERE history.track_id = tracks.id)
              AND (play_count = 0 OR play_count IS NULL);
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_library ON tracks(is_downloaded, play_count, last_played)")
    conn.close()

# Singer Operations
def get_taken_avatars_for_name(name: str, exclude_token: str = "") -> List[str]:
    """Returns list of avatar emojis already claimed by other singers for this specific name."""
    clean_name = name.strip()
    if not clean_name:
        return []
    conn = get_db()
    cursor = conn.execute("""
        SELECT avatar FROM singers 
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND token != ?
    """, (clean_name, exclude_token))
    rows = cursor.fetchall()
    conn.close()
    return [r["avatar"] for r in rows if r["avatar"]]

def check_singer_avatar_available(name: str, avatar: str, exclude_token: str = "") -> bool:
    clean_name = name.strip()
    if not clean_name:
        return False
    conn = get_db()
    cursor = conn.execute("""
        SELECT COUNT(*) as cnt FROM singers 
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND avatar = ? AND token != ?
    """, (clean_name, avatar, exclude_token))
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] == 0

def get_or_create_singer(token: str, name: str = "", avatar: str = "🌺", ip_address: str = "") -> Dict[str, Any]:
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

        if row:
            target_name = clean_name if clean_name else row["name"]
            target_avatar = clean_avatar if avatar else row["avatar"]

            # If changing name or avatar, ensure the (name, avatar) combo is not claimed by another token
            if target_name.lower() != row["name"].lower() or target_avatar != row["avatar"]:
                chk = conn.execute("""
                    SELECT COUNT(*) as cnt FROM singers 
                    WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND avatar = ? AND token != ?
                """, (target_name, target_avatar, clean_token)).fetchone()
                if chk["cnt"] > 0:
                    raise ValueError(f"'{target_name}' with the {target_avatar} avatar is already taken! Please pick another avatar.")

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
            chk = conn.execute("""
                SELECT COUNT(*) as cnt FROM singers 
                WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND avatar = ? AND token != ?
            """, (final_name, clean_avatar, clean_token)).fetchone()
            if chk["cnt"] > 0:
                raise ValueError(f"'{final_name}' with the {clean_avatar} avatar is already taken! Please pick another avatar.")

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

def get_singers_with_queue() -> List[Dict[str, Any]]:
    """Returns all singers with their queued songs for host roster management."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            s.id as singer_id,
            s.name as singer_name,
            s.avatar as singer_avatar,
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
                q.sort_order,
                t.id as track_id,
                t.title,
                t.artist,
                t.duration
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

def purge_all_singers(include_history: bool = True) -> Dict[str, int]:
    """Purges all test/registered singers and associated queue/history records while preserving tracks and settings."""
    conn = get_db()
    with conn:
        q_cnt = conn.execute("DELETE FROM queue").rowcount
        h_cnt = conn.execute("DELETE FROM history").rowcount if include_history else 0
        s_cnt = conn.execute("DELETE FROM singers").rowcount
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('queue', 'history', 'singers')")
    conn.close()
    return {"singers_deleted": s_cnt, "queue_deleted": q_cnt, "history_deleted": h_cnt}

# Track Operations
def save_track(video_id: str, title: str, artist: str, original_title: str, channel: str = "", duration: int = 0) -> int:
    conn = get_db()
    with conn:
        cursor = conn.execute("""
            INSERT INTO tracks (video_id, title, artist, original_title, channel, duration)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                original_title = excluded.original_title,
                channel = excluded.channel,
                duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE tracks.duration END
        """, (video_id, title, artist, original_title, channel, duration))
        
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

def update_track_download_status(track_id: int, status: str, local_path: str = "", normalized_lufs: float = -16.0):
    conn = get_db()
    with conn:
        is_dl = 1 if status == "ready" and local_path else 0
        conn.execute("""
            UPDATE tracks
            SET download_status = ?,
                is_downloaded = CASE WHEN ? = 1 THEN 1 ELSE is_downloaded END,
                local_path = CASE WHEN ? != '' THEN ? ELSE local_path END,
                normalized_lufs = ?
            WHERE id = ?
        """, (status, is_dl, local_path, local_path, normalized_lufs, track_id))
    conn.close()

def search_local_tracks(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    conn = get_db()
    clean_q = "".join(c for c in query if c.isalnum() or c.isspace()).strip()
    if not clean_q:
        cursor = conn.execute("SELECT * FROM tracks WHERE is_downloaded = 1 ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    fts_query = f'"{clean_q}"* OR ' + " ".join(f'"{word}"*' for word in clean_q.split())
    try:
        cursor = conn.execute("""
            SELECT t.*, rank
            FROM tracks t
            JOIN tracks_fts f ON t.id = f.rowid
            WHERE tracks_fts MATCH ? AND t.is_downloaded = 1
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit))
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        like_str = f"%{clean_q}%"
        cursor = conn.execute("""
            SELECT * FROM tracks
            WHERE is_downloaded = 1 AND (title LIKE ? OR artist LIKE ? OR original_title LIKE ?)
            LIMIT ?
        """, (like_str, like_str, like_str, limit))
        rows = cursor.fetchall()

    conn.close()
    return [dict(r) for r in rows]

# Queue Operations & Fair Rotation
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
                "effective_round": effective_round,
                "completed_count": completed_count,
                "last_played": last_played,
                "created_at": r["created_at"]
            })

        # Sort key:
        # (1) effective_round ASC (Round 1 songs before Round 2)
        # (2) completed_count ASC (Singers with fewer completed songs go first in their round)
        # (3) last_played ASC ("" sorts first for singers who haven't sung yet tonight)
        # (4) created_at ASC (Earlier queue time)
        # (5) id ASC
        annotated.sort(key=lambda x: (
            x["effective_round"],
            x["completed_count"],
            x["last_played"],
            x["created_at"],
            x["id"]
        ))

        # Update sort_order in database
        for idx, item in enumerate(annotated):
            conn.execute("UPDATE queue SET sort_order = ? WHERE id = ?", (float(idx + 1), item["id"]))
        if close_conn:
            conn.commit()

    finally:
        if close_conn:
            conn.close()

def enqueue_song(track_id: int, singer_id: int, duet_partner: str = "", pitch_shift: int = 0) -> int:
    conn = get_db()
    with conn:
        cursor = conn.execute("""
            INSERT INTO queue (track_id, singer_id, duet_partner, pitch_shift, sort_order)
            VALUES (?, ?, ?, ?, 999999.0)
        """, (track_id, singer_id, duet_partner, pitch_shift))
        queue_id = cursor.lastrowid
        rebalance_queue_round_robin(conn)
    conn.close()
    return queue_id

def get_queue() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            q.id as queue_id,
            q.status,
            q.pitch_shift,
            q.duet_partner,
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
            s.id as singer_id,
            s.name as singer_name,
            s.avatar as singer_avatar
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

def get_current_playing() -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.execute("""
        SELECT 
            q.id as queue_id,
            q.pitch_shift,
            q.duet_partner,
            q.played_at,
            t.id as track_id,
            t.video_id,
            t.title,
            t.artist,
            t.duration,
            t.is_downloaded,
            t.local_path,
            s.name as singer_name,
            s.avatar as singer_avatar
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
            played_dt = datetime.datetime.strptime(data["played_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
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
            conn.execute("UPDATE queue SET status = 'completed' WHERE status = 'playing'")
            conn.execute("UPDATE queue SET status = 'playing', played_at = CURRENT_TIMESTAMP WHERE id = ?", (queue_id,))
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

def reorder_queue(ordered_queue_ids: List[int]):
    conn = get_db()
    with conn:
        for idx, q_id in enumerate(ordered_queue_ids):
            conn.execute("UPDATE queue SET sort_order = ? WHERE id = ?", (float(idx + 1), q_id))
    conn.close()

# History & Stats
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
    search: str = ""
) -> Dict[str, Any]:
    """Returns paginated downloaded tracks with custom sorting: popularity, bring_back, recent, least_played, alpha."""
    conn = get_db()
    conditions = ["is_downloaded = 1"]
    params: List[Any] = []

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
        cnt_5 = conn.execute("SELECT COUNT(*) as c FROM tracks WHERE is_downloaded = 1 AND play_count >= 5").fetchone()["c"]
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
               created_at, coalesce(play_count, 0) as play_count, last_played
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

def get_unique_downloaded_artists() -> List[str]:
    """Returns distinct artists who have downloaded tracks in the library."""
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT artist 
        FROM tracks 
        WHERE is_downloaded = 1 AND artist != ''
        ORDER BY artist COLLATE NOCASE ASC
    """).fetchall()
    conn.close()
    return [r["artist"] for r in rows]

def get_cleanup_candidates(sort_by: str = "oldest_played", limit: int = 50) -> List[Dict[str, Any]]:
    """Returns candidate tracks for pruning storage sorted by longest unplayed or least played."""
    conn = get_db()
    if sort_by == "least_played":
        order_clause = "ORDER BY play_count ASC, coalesce(last_played, created_at) ASC"
    else: # oldest_played
        order_clause = "ORDER BY coalesce(last_played, '1970-01-01') ASC, play_count ASC"

    query = f"""
        SELECT id, video_id, title, artist, duration, local_path,
               coalesce(play_count, 0) as play_count, last_played, created_at
        FROM tracks
        WHERE is_downloaded = 1 AND local_path != ''
        {order_clause}
        LIMIT ?
    """
    rows = conn.execute(query, (limit,)).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        p = Path(d["local_path"])
        size_bytes = p.stat().st_size if p.exists() else 0
        d["size_bytes"] = size_bytes
        d["size_mb"] = round(size_bytes / (1024 * 1024), 1)
        results.append(d)
    return results

def prune_local_track(track_id: int) -> bool:
    """Removes the downloaded video file on disk while keeping track metadata & history intact."""
    conn = get_db()
    track = conn.execute("SELECT local_path FROM tracks WHERE id = ?", (track_id,)).fetchone()
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
        conn.execute("""
            UPDATE tracks
            SET is_downloaded = 0,
                local_path = '',
                download_status = 'idle'
            WHERE id = ?
        """, (track_id,))
    conn.close()
    return True


