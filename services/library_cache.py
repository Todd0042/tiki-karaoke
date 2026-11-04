"""
services/library_cache.py - In-memory high-performance catalog cache for Tiki Karaoke.

Preloads and caches all downloaded local library tracks, unique artists, and catalog metadata
to deliver sub-millisecond search and instant library browsing without repeated disk I/O.
"""

import time
import threading
from typing import List, Dict, Any, Optional
from database import get_browsable_tracks, get_unique_downloaded_artists, search_local_tracks

_cache_lock = threading.Lock()
_cached_tracks: Optional[List[Dict[str, Any]]] = None
_cached_artists: Dict[bool, List[str]] = {}  # sing_along -> list of artists
_last_refresh: float = 0.0
_CACHE_TTL: float = 300.0  # 5 minutes background TTL if not explicitly invalidated


def warm_library_cache(force: bool = False) -> None:
    """Preloads the local library catalog into memory."""
    global _cached_tracks, _cached_artists, _last_refresh
    now = time.time()
    with _cache_lock:
        if not force and _cached_tracks is not None and (now - _last_refresh < _CACHE_TTL):
            return
        try:
            # Load full browsable tracks catalog
            catalog = get_browsable_tracks(page=1, limit=50000, sort="title", sing_along=False)
            _cached_tracks = catalog.get("tracks", [])
            _cached_artists[False] = get_unique_downloaded_artists(sing_along=False)
            _cached_artists[True] = get_unique_downloaded_artists(sing_along=True)
            _last_refresh = now
        except Exception as e:
            print(f"[LibraryCache] Error warming cache: {e}")


def invalidate_library_cache() -> None:
    """Forces cache refresh on next access (e.g. after download, prune, or metadata edit)."""
    global _cached_tracks, _cached_artists, _last_refresh
    with _cache_lock:
        _cached_tracks = None
        _cached_artists.clear()
        _last_refresh = 0.0


def get_cached_unique_artists(sing_along: bool = False) -> List[str]:
    """Returns cached list of unique downloaded artists instantly."""
    global _cached_artists
    if sing_along not in _cached_artists:
        warm_library_cache()
    with _cache_lock:
        return list(_cached_artists.get(sing_along, []))


def search_cached_tracks(query: str, limit: int = 25, sing_along_only: bool = False) -> List[Dict[str, Any]]:
    """
    Sub-millisecond in-memory fuzzy/substring search over local library tracks.
    Falls back to SQLite FTS5 if cache is empty or warming.
    """
    if _cached_tracks is None:
        warm_library_cache()

    q_lower = query.strip().lower()
    if not q_lower:
        return []

    with _cache_lock:
        if not _cached_tracks:
            return search_local_tracks(query, limit=limit, sing_along_only=sing_along_only)

        tokens = q_lower.split()
        matches = []
        for track in _cached_tracks:
            if sing_along_only and not track.get("is_sing_along"):
                continue

            title = str(track.get("title", "")).lower()
            artist = str(track.get("artist", "")).lower()
            orig = str(track.get("original_title", "")).lower()
            combined = f"{artist} {title} {orig}"

            # Check all search tokens match
            if all(tok in combined for tok in tokens):
                matches.append(track)
                if len(matches) >= limit:
                    break

        return matches
