import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import hashlib
import secrets
import shutil
import re
from config import (
    get_db_path, get_data_dir, ensure_storage_dirs, DB_PATH,
    get_tracks_dir, get_sing_along_dir, get_filler_dir
)

TRACKS_TABLE_SQL = """
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
    download_status TEXT DEFAULT 'idle',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    play_count INTEGER DEFAULT 0,
    last_played TIMESTAMP,
    is_sing_along INTEGER DEFAULT 0,
    genre TEXT DEFAULT '',
    release_year INTEGER DEFAULT 0,
    media_source TEXT DEFAULT 'youtube',
    media_type TEXT DEFAULT 'video',
    cdg_path TEXT DEFAULT ''
)
"""

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

