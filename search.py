import re
from typing import List, Dict, Any, Tuple
import yt_dlp
from rapidfuzz import fuzz
from database import search_local_tracks, save_track

# Regular expressions for cleaning metadata
CHANNEL_BRANDS = [
    r"sing\s*king", r"karafun", r"zoom\s*karaoke", r"edkara", r"starlight\s*karaoke",
    r"cckaraoke", r"sunfly", r"party\s*tyme", r"coversph", r"divejam"
]

CLUTTER_PATTERNS = [
    r"\[.*?\]",
    r"【.*?】",
    r"\(.*?\bkari?aoke\b.*?\)",
    r"\(.*?\bwith\s+lyrics\b.*?\)",
    r"\(.*?\bofficial\s+video\b.*?\)",
    r"\(.*?\binstrumental\b.*?\)",
    r"\bkaraoke\s+version\b",
    r"\bkaraoke\b",
    r"\bofficial\s+audio\b",
    r"\blyrics\s+on\s+screen\b",
    r"\b4k\s+60fps\b",
    r"\bhd\b",
    r"\b4k\b",
    r"\bhi-?res\b",
    r"♬",
]

def extract_attributes(raw_title: str) -> List[str]:
    attrs = []
    lower = raw_title.lower()
    
    # Backing vocals / Guide melody
    if "no guide melody" in lower or "without guide" in lower or "no vocal" in lower:
        attrs.append("No Guide Melody")
    elif "guide melody" in lower or "with melody" in lower:
        attrs.append("With Guide Melody")
        
    if "backing vocals" in lower or "with backing" in lower or "bvs" in lower:
        attrs.append("With Backing Vocals")
    elif "no backing vocals" in lower or "clean instrumental" in lower or "off vocal" in lower:
        attrs.append("No Backing Vocals")

    # Key / Pitch Transposition notes in YouTube title
    key_match = re.search(r"([+-]\d+)\s*(?:semitones?|halftones?|keys?|steps?)", lower)
    if key_match:
        attrs.append(f"Key {key_match.group(1)}")
    elif "lower key" in lower:
        attrs.append("Lower Key")
    elif "higher key" in lower:
        attrs.append("Higher Key")
    elif "female key" in lower:
        attrs.append("Female Key")
    elif "male key" in lower:
        attrs.append("Male Key")
        
    return attrs

def normalize_title_artist(raw_title: str, uploader: str = "") -> Tuple[str, str]:
    cleaned = raw_title
    for brand in CHANNEL_BRANDS:
        cleaned = re.sub(rf"\b{brand}\b", "", cleaned, flags=re.IGNORECASE)
    for pat in CLUTTER_PATTERNS:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)

    # Clean separators and extra whitespace
    cleaned = re.sub(r"[\t\r\n|/]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:|~_")

    artist = ""
    title = cleaned

    # Try to split by "-" or ":"
    if " - " in cleaned:
        parts = cleaned.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()
    elif ":" in cleaned:
        parts = cleaned.split(":", 1)
        artist = parts[0].strip()
        title = parts[1].strip()
    elif " by " in cleaned.lower():
        parts = re.split(r"\s+by\s+", cleaned, flags=re.IGNORECASE, maxsplit=1)
        title = parts[0].strip()
        artist = parts[1].strip()

    # If artist is still unknown, try uploader if not a generic karaoke channel
    if not artist and uploader:
        up_clean = uploader.lower()
        if not any(brand in up_clean for brand in ["karaoke", "king", "sing", "instrumental"]):
            artist = uploader

    return artist.strip(" -"), title.strip(" -")

def deduplicate_and_group(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []

    for entry in entries:
        raw_title = entry.get("title", "")
        uploader = entry.get("uploader", "") or entry.get("channel", "")
        artist, title = normalize_title_artist(raw_title, uploader)
        
        display_title = title if title else raw_title
        display_artist = artist if artist else (uploader or "Unknown Artist")
        attributes = extract_attributes(raw_title)

        version_info = {
            "video_id": entry.get("id"),
            "raw_title": raw_title,
            "uploader": uploader,
            "duration": entry.get("duration", 0),
            "attributes": attributes,
            "channel": uploader,
            "thumbnail": f"https://i.ytimg.com/vi/{entry.get('id')}/hqdefault.jpg"
        }

        # Check if matches an existing group
        match_found = False
        track_key = f"{display_artist} {display_title}".lower()

        for group in groups:
            group_key = f"{group['artist']} {group['title']}".lower()
            similarity = fuzz.token_sort_ratio(track_key, group_key)
            if similarity >= 82:
                group["versions"].append(version_info)
                match_found = True
                break

        if not match_found:
            groups.append({
                "artist": display_artist,
                "title": display_title,
                "best_video_id": entry.get("id"),
                "duration": entry.get("duration", 0),
                "versions": [version_info]
            })

    return groups

def is_karaoke_track(title: str, uploader: str = "") -> bool:
    """Strictly validates whether a track is a legitimate karaoke/instrumental backing track."""
    t_lower = title.lower()
    u_lower = uploader.lower()

    # Disqualifying negative patterns (music videos, reactions, covers with singing, tutorials)
    negative_patterns = [
        r"\bofficial\s+music\s+video\b",
        r"\bofficial\s+video\b",
        r"\bmusic\s+video\b",
        r"\bofficial\s+audio\b",
        r"\blive\s+at\b",
        r"\blive\s+in\b",
        r"\bconcert\b",
        r"\breaction\b",
        r"\breacts\b",
        r"\bvocal\s+coach\b",
        r"\bhow\s+to\s+sing\b",
        r"\bhow\s+to\s+play\b",
        r"\blesson\b",
        r"\btutorial\b",
        r"\bguitar\s+tab\b",
        r"\bbass\s+tab\b",
        r"\bcover\s+by\b",
        r"\bfull\s+album\b",
        r"\bdiscography\b",
        r"\bteaser\b",
        r"\btrailer\b",
        r"\bparody\b"
    ]

    has_negative = any(re.search(pat, t_lower) for pat in negative_patterns)

    # Positive karaoke indicators
    karaoke_title_keywords = [
        "karaoke", "instrumental", "backing track", "backingtrack", "backing-track",
        "minus one", "minus 1", "minusone", "off vocal", "no vocal", "vocal off",
        "clean backing", "sing along", "sing-along", "singalong", "sing king",
        "karafun", "sunfly", "acoustic backing", "piano backing", "guitar backing"
    ]
    has_karaoke_title = any(kw in t_lower for kw in karaoke_title_keywords)

    karaoke_channels = [
        "karaoke", "karafun", "sing king", "singking", "sunfly", "edkara",
        "zoom karaoke", "starlight karaoke", "party tyme", "coversph",
        "divejam", "instrumental", "backing track", "cckaraoke"
    ]
    is_karaoke_channel = any(kw in u_lower for kw in karaoke_channels)

    # If it matched a negative pattern (e.g. 'official music video'), only keep if it EXPLICITLY says karaoke or instrumental
    if has_negative:
        if not ("karaoke" in t_lower or "instrumental" in t_lower or "backing track" in t_lower):
            return False

    # Must have explicit karaoke in title or be uploaded by a recognized karaoke producer
    return has_karaoke_title or is_karaoke_channel

def query_youtube_karaoke(query: str, max_results: int = 15) -> List[Dict[str, Any]]:
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "no_warnings": True,
    }
    # Query more candidates so we have plenty after strict filtering
    fetch_count = max(max_results * 2, 20)
    search_q = f"ytsearch{fetch_count}:{query} karaoke"
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(search_q, download=False)
            raw_entries = info.get("entries", [])
        except Exception as e:
            print(f"[Search] YouTube query error: {e}")
            raw_entries = []

    # Filter strictly for genuine karaoke/instrumental versions only
    valid_entries = []
    for e in raw_entries:
        if not e or not e.get("id"):
            continue
        title = e.get("title", "")
        uploader = e.get("uploader", "") or e.get("channel", "")
        if is_karaoke_track(title, uploader):
            valid_entries.append(e)

    # Save discovered metadata to DB tracks for easy future reference
    for entry in valid_entries:
        artist, title = normalize_title_artist(entry.get("title", ""), entry.get("uploader", ""))
        save_track(
            video_id=entry.get("id"),
            title=title if title else entry.get("title", ""),
            artist=artist if artist else "Unknown Artist",
            original_title=entry.get("title", ""),
            channel=entry.get("uploader", ""),
            duration=entry.get("duration", 0) or 0
        )

    return deduplicate_and_group(valid_entries)[:max_results]

def tiered_search(query: str, force_online: bool = False) -> Dict[str, Any]:
    query_str = query.strip()
    if not query_str:
        return {"local_results": [], "youtube_results": []}

    local_matches = search_local_tracks(query_str, limit=10)
    
    # If we have local matches and user didn't ask to force online search, return local first!
    if local_matches and not force_online:
        return {
            "local_results": local_matches,
            "youtube_results": []
        }

    # Query YouTube online
    yt_results = query_youtube_karaoke(query_str, max_results=12)

    return {
        "local_results": local_matches,
        "youtube_results": yt_results
    }
