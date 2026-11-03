import os
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional
import requests

try:
    from rapidfuzz import fuzz
    def text_similarity(a: str, b: str) -> float:
        return float(fuzz.ratio(a.lower(), b.lower()))
except ImportError:
    from difflib import SequenceMatcher
    def text_similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100.0

CACHE_DB_PATH = Path(__file__).resolve().parent.parent / "importer_cache.db"


class MetadataEnricher:
    """
    Enriches track metadata using the public iTunes Search API and an offline SQLite cache.
    Automatically resolves swapped Artist/Title orders and populates Release Year and Genre.
    """
    def __init__(self, cache_path: Optional[Path] = None):
        self.cache_path = cache_path or CACHE_DB_PATH
        self._init_cache()

    def _init_cache(self):
        with sqlite3.connect(self.cache_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata_cache (
                    query_key TEXT PRIMARY KEY,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    release_year INTEGER DEFAULT 0,
                    genre TEXT DEFAULT '',
                    artwork_url TEXT DEFAULT '',
                    is_found INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _get_from_cache(self, key: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.cache_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM metadata_cache WHERE query_key = ?", (key,)).fetchone()
            if row:
                return dict(row)
        return None

    def _save_to_cache(self, key: str, data: Optional[Dict[str, Any]]):
        with sqlite3.connect(self.cache_path) as conn:
            if data:
                conn.execute("""
                    INSERT OR REPLACE INTO metadata_cache (query_key, artist, title, release_year, genre, artwork_url, is_found)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (
                    key,
                    data.get("artist", ""),
                    data.get("title", ""),
                    data.get("release_year", 0),
                    data.get("genre", ""),
                    data.get("artwork_url", "")
                ))
            else:
                conn.execute("""
                    INSERT OR REPLACE INTO metadata_cache (query_key, artist, title, release_year, genre, artwork_url, is_found)
                    VALUES (?, '', '', 0, '', '', 0)
                """, (key,))

    def enrich(self, raw_artist: str, raw_title: str) -> Dict[str, Any]:
        """
        Enriches the artist and title with official capitalization, year, and genre.
        Returns the original values if no web match is found.
        """
        cleaned_a = raw_artist.strip()
        cleaned_t = raw_title.strip()
        
        # Don't look up generic unknown names
        if cleaned_a.lower() in {"karaoke track", "unknown artist", "unknown"}:
            query_str = cleaned_t
        else:
            query_str = f"{cleaned_a} {cleaned_t}"

        cache_key = f"{cleaned_a.lower()}:::{cleaned_t.lower()}"
        cached = self._get_from_cache(cache_key)
        if cached:
            if cached.get("is_found") and cached.get("artist") and cached.get("title"):
                return {
                    "artist": cached["artist"],
                    "title": cached["title"],
                    "release_year": cached["release_year"],
                    "genre": cached["genre"],
                    "artwork_url": cached["artwork_url"],
                    "enriched": True,
                    "swapped": False,
                }
            return {
                "artist": cleaned_a,
                "title": cleaned_t,
                "release_year": 0,
                "genre": "",
                "artwork_url": "",
                "enriched": False,
                "swapped": False,
            }

        # Query public iTunes Search API (50ms response, zero auth, free)
        try:
            url = f"https://itunes.apple.com/search?term={urllib.parse.quote(query_str)}&entity=song&limit=5"
            resp = requests.get(url, timeout=4, headers={"User-Agent": "TikiKaraokeImporter/1.0"})
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    best_match = None
                    best_score = 0
                    is_swapped = False

                    for r in results:
                        cand_artist = r.get("artistName", "")
                        cand_title = r.get("trackName", "")

                        # Normal Order Match: cand_artist ~ cleaned_a AND cand_title ~ cleaned_t
                        score_a = text_similarity(cleaned_a, cand_artist)
                        score_t = text_similarity(cleaned_t, cand_title)
                        normal_score = (score_a * 0.5) + (score_t * 0.5)

                        # Inverted Order Match: cand_artist ~ cleaned_t AND cand_title ~ cleaned_a
                        swap_score_a = text_similarity(cleaned_t, cand_artist)
                        swap_score_t = text_similarity(cleaned_a, cand_title)
                        swap_score = (swap_score_a * 0.5) + (swap_score_t * 0.5)

                        if normal_score > best_score and normal_score >= 60:
                            best_score = normal_score
                            best_match = (cand_artist, cand_title, r)
                            is_swapped = False

                        if swap_score > best_score and swap_score >= 60:
                            best_score = swap_score
                            best_match = (cand_artist, cand_title, r)
                            is_swapped = True

                    if best_match:
                        matched_artist, matched_title, raw_item = best_match
                        release_date = raw_item.get("releaseDate", "")
                        year = int(release_date[:4]) if len(release_date) >= 4 and release_date[:4].isdigit() else 0
                        genre = raw_item.get("primaryGenreName", "")
                        art = raw_item.get("artworkUrl100", "")

                        enriched_result = {
                            "artist": matched_artist,
                            "title": matched_title,
                            "release_year": year,
                            "genre": genre,
                            "artwork_url": art,
                            "enriched": True,
                            "swapped": is_swapped,
                        }
                        self._save_to_cache(cache_key, enriched_result)
                        return enriched_result

            # Not found
            self._save_to_cache(cache_key, None)
        except Exception:
            # On network timeout or offline mode, fail gracefully
            pass

        return {
            "artist": cleaned_a,
            "title": cleaned_t,
            "release_year": 0,
            "genre": "",
            "artwork_url": "",
            "enriched": False,
            "swapped": False,
        }
