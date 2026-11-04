import os
import re
import time
import json
import urllib.parse
import urllib.request
from typing import Dict, Any, Optional, Tuple

USER_AGENT = "TikiKaraoke/1.0 (https://github.com/tiki-karaoke/tiki-karaoke)"

STANDARD_GENRES = [
    "Rock", "Pop", "Country", "Hip-Hop", "R&B", "Dance",
    "Electronic", "Soul", "Reggae", "Jazz", "Blues", "Metal",
    "Folk", "Indie", "Alternative", "Latin", "Funk", "Disco"
]

def canonicalize_genre(raw_genre: str) -> str:
    """Normalizes raw genre strings into clean recognizable categories."""
    if not raw_genre:
        return "Pop"
    g_lower = raw_genre.lower()

    if any(k in g_lower for k in ["rock", "grunge", "punk"]):
        return "Rock"
    if any(k in g_lower for k in ["hip-hop", "hip hop", "hiphop", "rap", "trap"]):
        return "Hip-Hop"
    if any(k in g_lower for k in ["r&b", "soul", "neo-soul"]):
        return "R&B"
    if any(k in g_lower for k in ["country", "americana", "bluegrass"]):
        return "Country"
    if any(k in g_lower for k in ["dance", "electronic", "house", "edm", "techno", "synth"]):
        return "Dance"
    if any(k in g_lower for k in ["reggae", "dancehall", "ska"]):
        return "Reggae"
    if any(k in g_lower for k in ["latin", "reggaeton", "salsa", "bachata"]):
        return "Latin"
    if any(k in g_lower for k in ["metal", "heavy metal"]):
        return "Metal"
    if any(k in g_lower for k in ["disco", "funk"]):
        return "Disco"
    if any(k in g_lower for k in ["folk", "acoustic"]):
        return "Folk"
    if any(k in g_lower for k in ["jazz", "blues"]):
        return "Jazz"
    if any(k in g_lower for k in ["pop"]):
        return "Pop"

    # Fallback to Title-cased raw genre
    return raw_genre.strip().title()

def fetch_itunes_metadata(artist: str, title: str) -> Optional[Dict[str, Any]]:
    """Fetches primary genre and release year from iTunes Search API."""
    term = f"{artist} {title}".strip()
    if not term:
        return None

    query = urllib.parse.quote(term)
    url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            results = data.get("results", [])
            if results:
                best = results[0]
                raw_genre = best.get("primaryGenreName", "")
                release_date = best.get("releaseDate", "")
                year = 0
                if release_date and len(release_date) >= 4:
                    try:
                        year = int(release_date[:4])
                    except ValueError:
                        year = 0

                return {
                    "genre": canonicalize_genre(raw_genre),
                    "release_year": year,
                    "artist": best.get("artistName", artist),
                    "title": best.get("trackName", title),
                    "source": "itunes"
                }
    except Exception as e:
        print(f"[Metadata] iTunes query failed for '{term}': {e}")

    return None

def fetch_wikipedia_metadata(artist: str, title: str) -> Optional[Dict[str, Any]]:
    """Fallback fetcher querying Wikipedia Search and Summary API."""
    term = f"{artist} {title} song".strip()
    query = urllib.parse.quote(term)
    search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json&utf8=1"

    req = urllib.request.Request(search_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            search_results = data.get("query", {}).get("search", [])
            if not search_results:
                return None
            page_title = search_results[0]["title"]

        # Fetch page summary
        summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(page_title)}"
        s_req = urllib.request.Request(summary_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(s_req, timeout=5) as resp:
            summary_data = json.loads(resp.read().decode("utf-8", errors="replace"))
            extract = summary_data.get("extract", "")

            # Look for 4-digit release year (1920 - 2029)
            year = 0
            year_match = re.search(r"\b(19[2-9]\d|20[0-2]\d)\b", extract)
            if year_match:
                year = int(year_match.group(1))

            # Detect genre keyword
            genre = "Pop"
            for g in STANDARD_GENRES:
                if re.search(rf"\b{g}\b", extract, re.IGNORECASE):
                    genre = canonicalize_genre(g)
                    break

            return {
                "genre": genre,
                "release_year": year,
                "source": "wikipedia"
            }
    except Exception as e:
        print(f"[Metadata] Wikipedia fallback failed for '{term}': {e}")

_METADATA_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_METADATA_CACHE_TTL: float = 3600.0


def fetch_track_metadata(artist: str, title: str) -> Dict[str, Any]:
    """
    Enriches track with genre and release year.
    Tries iTunes API first, falls back to Wikipedia, defaults to ('Pop', 0).
    Uses 1-hour TTL in-memory cache to prevent redundant outbound HTTP queries.
    """
    if os.environ.get("TIKI_TESTING"):
        return {"genre": "Pop", "release_year": 2000, "source": "test"}

    cache_key = f"{artist.strip().lower()} - {title.strip().lower()}"
    now = time.time()
    cached = _METADATA_CACHE.get(cache_key)
    if cached and (now - cached[0] < _METADATA_CACHE_TTL):
        return cached[1]

    # 1. Try iTunes (fast 3.0s timeout)
    itunes_res = fetch_itunes_metadata(artist, title)
    if itunes_res:
        _METADATA_CACHE[cache_key] = (now, itunes_res)
        return itunes_res

    # 2. Try Wikipedia (3.0s timeout)
    wiki_res = fetch_wikipedia_metadata(artist, title)
    if wiki_res:
        _METADATA_CACHE[cache_key] = (now, wiki_res)
        return wiki_res

    # 3. Default fallback
    fallback = {
        "genre": "Pop",
        "release_year": 0,
        "source": "default"
    }
    _METADATA_CACHE[cache_key] = (now, fallback)
    return fallback
