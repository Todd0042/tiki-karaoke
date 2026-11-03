import sqlite3
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, Set, List


class TikiDatabaseAdapter:
    """
    Connects to the Tiki Karaoke SQLite database and performs safe, schema-compliant
    track inserts and updates.
    """
    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).resolve())
        self._ensure_connection()

    def _ensure_connection(self):
        p = Path(self.db_path)
        if not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        # Verify schema or create table if missing
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
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
            """)

    def get_existing_paths(self) -> Set[str]:
        """Returns a set of all normalized local_paths already in the database."""
        paths = set()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT local_path, cdg_path FROM tracks WHERE is_downloaded = 1").fetchall()
            for lp, cp in rows:
                if lp:
                    paths.add(str(Path(lp).resolve()).lower())
                if cp:
                    paths.add(str(Path(cp).resolve()).lower())
        return paths

    def insert_track(
        self,
        file_path: str,
        artist: str,
        title: str,
        duration: int = 210,
        release_year: int = 0,
        genre: str = "",
        channel: str = "Imported",
        is_sing_along: bool = False,
        media_type: str = "video",
        cdg_path: str = ""
    ) -> int:
        """Inserts or updates a track record matching Tiki Karaoke's database specifications."""
        resolved = str(Path(file_path).resolve())
        resolved_cdg = str(Path(cdg_path).resolve()) if cdg_path else ""
        vid = f"local_{hashlib.sha256((resolved + resolved_cdg).encode()).hexdigest()[:12]}"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO tracks (
                    video_id, title, artist, original_title, channel, duration,
                    is_downloaded, local_path, download_status, normalized_lufs,
                    is_sing_along, genre, release_year, media_source, media_type, cdg_path
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'ready', -16.0, ?, ?, ?, 'imported', ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title = excluded.title,
                    artist = excluded.artist,
                    channel = excluded.channel,
                    duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE tracks.duration END,
                    local_path = excluded.local_path,
                    is_downloaded = 1,
                    download_status = 'ready',
                    is_sing_along = excluded.is_sing_along,
                    genre = CASE WHEN excluded.genre != '' THEN excluded.genre ELSE tracks.genre END,
                    release_year = CASE WHEN excluded.release_year > 0 THEN excluded.release_year ELSE tracks.release_year END,
                    media_type = excluded.media_type,
                    cdg_path = excluded.cdg_path
            """, (
                vid, title, artist, Path(file_path).name, channel, duration,
                resolved, 1 if is_sing_along else 0, genre, release_year, media_type, resolved_cdg
            ))
            track_id = cursor.lastrowid
            if not track_id:
                row = conn.execute("SELECT id FROM tracks WHERE video_id = ?", (vid,)).fetchone()
                track_id = row[0] if row else 0
        return track_id
