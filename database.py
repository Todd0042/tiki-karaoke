import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import hashlib
import shutil
import re
from config import (
    get_db_path, get_data_dir, ensure_storage_dirs, DB_PATH,
    get_tracks_dir, get_sing_along_dir, get_filler_dir
)

def get_db():
    ensure_storage_dirs()
    conn = sqlite3.connect(get_db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # Fallback for network file shares (SMB/UNC) which do not support WAL shared memory locking
        conn.execute("PRAGMA journal_mode=TRUNCATE")
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

        CREATE TABLE IF NOT EXISTS singer_song_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            singer_id INTEGER NOT NULL REFERENCES singers(id),
            track_id INTEGER NOT NULL REFERENCES tracks(id),
            pitch_shift INTEGER DEFAULT 0,
            duet_partner TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_singer_song_log_singer ON singer_song_log(singer_id, track_id);
        CREATE INDEX IF NOT EXISTS idx_singer_song_log_time ON singer_song_log(singer_id, created_at);

        CREATE TABLE IF NOT EXISTS filler_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            original_title TEXT NOT NULL,
            genre TEXT DEFAULT '',
            release_year INTEGER DEFAULT 0,
            duration INTEGER DEFAULT 0,
            is_downloaded INTEGER DEFAULT 0,
            local_path TEXT DEFAULT '',
            normalized_lufs REAL DEFAULT -16.0,
            download_status TEXT DEFAULT 'pending', -- 'pending', 'fetching_metadata', 'downloading', 'normalizing', 'ready', 'failed'
            download_progress INTEGER DEFAULT 0,
            error_message TEXT DEFAULT '',
            play_count INTEGER DEFAULT 0,
            last_played TIMESTAMP,
            sort_order REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS filler_play_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filler_id INTEGER NOT NULL,
            is_sing_along INTEGER DEFAULT 0,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_filler_play_history_id ON filler_play_history(id DESC);

        CREATE TABLE IF NOT EXISTS pending_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER REFERENCES tracks(id),
            filler_id INTEGER REFERENCES filler_tracks(id),
            singer_id INTEGER REFERENCES singers(id),
            duet_partner TEXT DEFAULT '',
            pitch_shift INTEGER DEFAULT 0,
            dedication TEXT DEFAULT '',
            progress REAL DEFAULT 0.0,
            status TEXT DEFAULT 'pending', -- 'pending', 'downloading', 'failed'
            error_message TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            quality_profile TEXT DEFAULT 'default', -- 'default', 'medium_360p', 'lowest'
            priority INTEGER DEFAULT 10,
            auto_promote INTEGER DEFAULT 1,
            is_starter_pack INTEGER DEFAULT 0,
            starter_pack_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_pending_downloads_status ON pending_downloads(status);

        -- Default settings
        INSERT OR IGNORE INTO settings (key, value) VALUES ('tts_enabled', 'false');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('intermission_seconds', '15');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bgm_enabled', 'true');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bgm_volume', '0.25');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bar_name', 'The Tiki Hut Karaoke Lounge');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bgm_transition_policy', 'fade');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bgm_video_enabled', 'true');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bgm_mode', 'vibe');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('bgm_buffer_seconds', '15');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('host_display_enabled', 'false');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('host_display', '1');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('intermission_vibe_mode', 'sing_along');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('strict_byom_only', 'false');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('hotspot_enabled', 'false');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('hotspot_ssid', '');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('hotspot_password', '');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('marquee_scroll_speed', '85');
        """)

        # Migration: Ensure strict_byom_only defaults to 'false' (online search ON by default)
        migrated_strict = conn.execute("SELECT value FROM settings WHERE key = 'strict_byom_default_v103'").fetchone()
        if not migrated_strict:
            conn.execute("UPDATE settings SET value = 'false' WHERE key = 'strict_byom_only' AND value = 'true'")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('strict_byom_default_v103', 'true')")

        # Ensure play_count, last_played, is_sing_along, genre, release_year, and media_source exist on tracks
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()]
        if "play_count" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN play_count INTEGER DEFAULT 0")
        if "last_played" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN last_played TIMESTAMP")
        if "is_sing_along" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN is_sing_along INTEGER DEFAULT 0")
        if "genre" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN genre TEXT DEFAULT ''")
        if "release_year" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN release_year INTEGER DEFAULT 0")
        if "media_source" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN media_source TEXT DEFAULT 'youtube'")
        if "media_type" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN media_type TEXT DEFAULT 'video'")
        if "cdg_path" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN cdg_path TEXT DEFAULT ''")
        if "is_starter_pack" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN is_starter_pack INTEGER DEFAULT 0")
        if "starter_pack_name" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN starter_pack_name TEXT DEFAULT ''")

        # Ensure media_source and starter pack columns exist on filler_tracks
        cols_filler = [r["name"] for r in conn.execute("PRAGMA table_info(filler_tracks)").fetchall()]
        if "media_source" not in cols_filler:
            conn.execute("ALTER TABLE filler_tracks ADD COLUMN media_source TEXT DEFAULT 'youtube'")
        if "is_starter_pack" not in cols_filler:
            conn.execute("ALTER TABLE filler_tracks ADD COLUMN is_starter_pack INTEGER DEFAULT 0")
        if "starter_pack_name" not in cols_filler:
            conn.execute("ALTER TABLE filler_tracks ADD COLUMN starter_pack_name TEXT DEFAULT ''")

        # Ensure pending_downloads priority, auto_promote, filler_id, and starter pack columns exist
        cols_pending = [r["name"] for r in conn.execute("PRAGMA table_info(pending_downloads)").fetchall()]
        if "filler_id" not in cols_pending:
            conn.execute("ALTER TABLE pending_downloads ADD COLUMN filler_id INTEGER DEFAULT 0")
        if "priority" not in cols_pending:
            conn.execute("ALTER TABLE pending_downloads ADD COLUMN priority INTEGER DEFAULT 10")
        if "auto_promote" not in cols_pending:
            conn.execute("ALTER TABLE pending_downloads ADD COLUMN auto_promote INTEGER DEFAULT 1")
        if "is_starter_pack" not in cols_pending:
            conn.execute("ALTER TABLE pending_downloads ADD COLUMN is_starter_pack INTEGER DEFAULT 0")
        if "starter_pack_name" not in cols_pending:
            conn.execute("ALTER TABLE pending_downloads ADD COLUMN starter_pack_name TEXT DEFAULT ''")

        # Ensure status and status_updated_at exist on singers
        cols_singers = [r["name"] for r in conn.execute("PRAGMA table_info(singers)").fetchall()]
        if "status" not in cols_singers:
            conn.execute("ALTER TABLE singers ADD COLUMN status TEXT DEFAULT 'active'")
        if "status_updated_at" not in cols_singers:
            conn.execute("ALTER TABLE singers ADD COLUMN status_updated_at TIMESTAMP")

        # Ensure dedication exists on queue
        cols_queue = [r["name"] for r in conn.execute("PRAGMA table_info(queue)").fetchall()]
        if "dedication" not in cols_queue:
            conn.execute("ALTER TABLE queue ADD COLUMN dedication TEXT DEFAULT ''")

        # Ensure recordings table exists for live singer audio captures
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_id INTEGER,
                singer_id INTEGER NOT NULL REFERENCES singers(id),
                track_id INTEGER NOT NULL REFERENCES tracks(id),
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                duration_seconds REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recordings_singer ON recordings(singer_id, created_at)")

        # Auto-migration for media_source tagging:
        # Imported BYOM files have video_id LIKE 'local_%'; all others are assumed YouTube
        conn.execute("""
            UPDATE tracks 
            SET media_source = 'imported' 
            WHERE video_id LIKE 'local_%';
        """)
        conn.execute("""
            UPDATE tracks 
            SET media_source = 'youtube' 
            WHERE video_id NOT LIKE 'local_%' 
              AND (media_source IS NULL OR media_source = '' OR media_source = 'stream');
        """)
        conn.execute("""
            UPDATE filler_tracks 
            SET media_source = 'imported' 
            WHERE video_id LIKE 'local_%';
        """)
        conn.execute("""
            UPDATE filler_tracks 
            SET media_source = 'youtube' 
            WHERE video_id NOT LIKE 'local_%' 
              AND (media_source IS NULL OR media_source = '' OR media_source = 'stream');
        """)

        # Backfill play counts and last_played from history if present
        conn.execute("""
            UPDATE tracks
            SET play_count = (SELECT COUNT(*) FROM history WHERE history.track_id = tracks.id),
                last_played = (SELECT MAX(played_at) FROM history WHERE history.track_id = tracks.id)
            WHERE EXISTS (SELECT 1 FROM history WHERE history.track_id = tracks.id)
              AND (play_count = 0 OR play_count IS NULL);
        """)

        # Backfill historical singer queue records into singer_song_log if missing
        conn.execute("""
            INSERT INTO singer_song_log (singer_id, track_id, pitch_shift, duet_partner, created_at)
            SELECT singer_id, track_id, pitch_shift, duet_partner, played_at FROM history
            WHERE NOT EXISTS (
                SELECT 1 FROM singer_song_log 
                WHERE singer_song_log.singer_id = history.singer_id 
                  AND singer_song_log.track_id = history.track_id
            );
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_library ON tracks(is_downloaded, play_count, last_played)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_sing_along ON tracks(is_sing_along, is_downloaded)")
    conn.close()

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

def check_singer_avatar_available(
    name: str, 
    avatar: str, 
    exclude_token: str = "", 
    client_ip: str = "", 
    is_host: bool = False
) -> bool:
    taken = get_taken_avatars_for_name(name, exclude_token=exclude_token, client_ip=client_ip, is_host=is_host)
    return avatar not in taken

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

        # Sort key:
        # (1) is_on_break ASC (Active singers play before singers on break)
        # (2) effective_round ASC (Round 1 songs before Round 2)
        # (3) completed_count ASC (Singers with fewer completed songs go first in their round)
        # (4) last_played ASC ("" sorts first for singers who haven't sung yet tonight)
        # (5) created_at ASC (Earlier queue time)
        # (6) id ASC
        annotated.sort(key=lambda x: (
            x["is_on_break"],
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

def reorder_queue(ordered_queue_ids: List[int]):
    conn = get_db()
    with conn:
        for idx, q_id in enumerate(ordered_queue_ids):
            conn.execute("UPDATE queue SET sort_order = ? WHERE id = ?", (float(idx + 1), q_id))
    conn.close()

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




