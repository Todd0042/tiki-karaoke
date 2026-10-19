#!/usr/bin/env python3
"""
Tiki Karaoke - Starter BGM Filler Crate Seeder
Seeds a curated batch of 15 iconic, crowd-pleasing party music videos
spanning all eras and genres (70s, 80s, 90s, 2000s, 2010s).
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from database import init_db, add_filler_track, get_filler_tracks
from downloader import enqueue_filler_download

STARTER_FILLER_TRACKS = [
    # 70s / 80s Classic Rock & Pop
    {
        "video_id": "fJ9rUzIMcZQ",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "genre": "Rock",
        "release_year": 1975,
        "duration": 360
    },
    {
        "video_id": "1k8craCGghs",
        "title": "Don't Stop Believin'",
        "artist": "Journey",
        "genre": "Rock",
        "release_year": 1981,
        "duration": 251
    },
    {
        "video_id": "Zi_XLOBDo_Y",
        "title": "Billie Jean",
        "artist": "Michael Jackson",
        "genre": "Pop",
        "release_year": 1982,
        "duration": 296
    },
    {
        "video_id": "PIb6AZdTr-A",
        "title": "Girls Just Want to Have Fun",
        "artist": "Cyndi Lauper",
        "genre": "Pop",
        "release_year": 1983,
        "duration": 267
    },
    # 90s Rock & Pop Hits
    {
        "video_id": "hTWKbfoikeg",
        "title": "Smells Like Teen Spirit",
        "artist": "Nirvana",
        "genre": "Rock",
        "release_year": 1991,
        "duration": 279
    },
    {
        "video_id": "TR3Vdo5etCQ",
        "title": "Don't Speak",
        "artist": "No Doubt",
        "genre": "Pop-Rock",
        "release_year": 1995,
        "duration": 302
    },
    {
        "video_id": "4fndeDfaWCg",
        "title": "I Want It That Way",
        "artist": "Backstreet Boys",
        "genre": "Pop",
        "release_year": 1999,
        "duration": 220
    },
    {
        "video_id": "L_jWHffIx5E",
        "title": "All Star",
        "artist": "Smash Mouth",
        "genre": "Pop-Rock",
        "release_year": 1999,
        "duration": 237
    },
    # 2000s Anthems
    {
        "video_id": "PWgvGjAhvIw",
        "title": "Hey Ya!",
        "artist": "Outkast",
        "genre": "Funk-Pop",
        "release_year": 2003,
        "duration": 295
    },
    {
        "video_id": "gGdGFtwCNBE",
        "title": "Mr. Brightside",
        "artist": "The Killers",
        "genre": "Indie Rock",
        "release_year": 2004,
        "duration": 228
    },
    {
        "video_id": "KUmZp8PR1uc",
        "title": "Rehab",
        "artist": "Amy Winehouse",
        "genre": "Soul",
        "release_year": 2006,
        "duration": 215
    },
    {
        "video_id": "ViwtNLUqkMY",
        "title": "Crazy In Love",
        "artist": "Beyoncé",
        "genre": "R&B-Pop",
        "release_year": 2003,
        "duration": 236
    },
    # 2010s Party Anthems
    {
        "video_id": "OPf0YbXqDm0",
        "title": "Uptown Funk",
        "artist": "Mark Ronson ft. Bruno Mars",
        "genre": "Funk-Pop",
        "release_year": 2014,
        "duration": 271
    },
    {
        "video_id": "450p7goxZqg",
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "genre": "Pop",
        "release_year": 2019,
        "duration": 263
    },
    {
        "video_id": "fRh_vgS2dFE",
        "title": "Sorry",
        "artist": "Justin Bieber",
        "genre": "Pop",
        "release_year": 2015,
        "duration": 206
    }
]

def seed_starter_crate() -> int:
    init_db()
    existing = {t["video_id"]: t for t in get_filler_tracks()}
    added_count = 0

    print("🌴 Tiki Karaoke Starter BGM Seeder")
    print("=" * 55)

    for track in STARTER_FILLER_TRACKS:
        vid = track["video_id"]
        if vid in existing:
            print(f"  ⏭️ Already in crate: {track['artist']} - {track['title']}")
            continue

        print(f"  ➕ Adding to crate: \033[1;36m{track['artist']} - {track['title']}\033[0m ({track['genre']}, {track['release_year']})")
        tid = add_filler_track(
            video_id=vid,
            title=track["title"],
            artist=track["artist"],
            original_title=f"{track['artist']} - {track['title']}",
            duration=track["duration"],
            genre=track["genre"],
            release_year=track["release_year"]
        )
        enqueue_filler_download(tid)
        added_count += 1

    print("=" * 55)
    print(f"✨ Successfully queued {added_count} starter filler tracks for download.")
    return added_count

if __name__ == "__main__":
    seed_starter_crate()
