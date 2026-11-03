import re
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

# Known Commercial Karaoke Brand / Manufacturer Codes (CompuHost & CDG Standards)
KARAOKE_BRANDS = {
    "SC": "Sound Choice",
    "CB": "Chartbuster",
    "SF": "Sunfly",
    "THM": "Top Hits Monthly",
    "PHM": "Pop Hits Monthly",
    "CHM": "Country Hits Monthly",
    "DK": "DK Karaoke",
    "SBI": "SBI Karaoke",
    "ZM": "Zoom Karaoke",
    "KFN": "KaraFun",
    "SAV": "Savvy",
    "LG": "Legends",
    "MM": "Monster Hits",
    "P": "Pioneer",
    "SK": "Sing It",
    "AMS": "Amerising",
    "BMB": "BMB",
    "STW": "Star Tracks",
    "TU": "Topline Ultimate",
    "MH": "MegaHits",
    "SGB": "Sweet Georgia Brown",
    "RS": "Radio Starz",
    "PT": "Party Tyme",
}

GENERIC_FOLDERS = {
    "tracks", "karaoke", "music", "videos", "video", "downloads", "download",
    "media", "songs", "library", "new folder", "temp", "tmp", "sing-along",
    "sing_along", "intermission", "filler", "youtube", "yt"
}

KNOWN_BRAND_FOLDERS = {
    "stingray karaoke": "Stingray",
    "priddis music": "Priddis",
    "party tyme karaoke": "Party Tyme",
    "party tyme": "Party Tyme",
    "karafun": "KaraFun",
    "sound choice": "Sound Choice",
    "chartbuster": "Chartbuster",
    "sunfly": "Sunfly",
    "zoom karaoke": "Zoom",
    "sbi karaoke": "SBI",
}


def invert_phonebook_name(name: str) -> str:
    """Converts 'Cash, Johnny' or 'Presley, Elvis' to 'Johnny Cash' and 'Elvis Presley'."""
    name = name.strip()
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            # Avoid flipping titles like 'Love, Me' or 'Goodbye, My Lover'
            lower_last = parts[0].lower()
            if lower_last in {"love", "goodbye", "hello", "hey", "remember", "oh", "baby"}:
                return name
            # Check if second part looks like a given name (1-3 words)
            return f"{parts[1]} {parts[0]}"
    return name


def clean_track_name(filename: str, parent_folder: Optional[str] = None) -> Dict[str, Any]:
    """
    Parses and sanitizes a raw karaoke filename.
    Extracts brand codes, disc/track IDs, cleans YouTube noise, and normalizes artist/title.
    If filename lacks an artist delimiter, uses parent_folder if non-generic.
    """
    stem = Path(filename).stem

    brand = ""
    disc_id = ""
    track_num = ""

    # Check parent folder for known karaoke brands
    if parent_folder:
        clean_pf = parent_folder.strip().lower()
        if clean_pf in KNOWN_BRAND_FOLDERS:
            brand = KNOWN_BRAND_FOLDERS[clean_pf]

    # 1. Extract Disc ID & Track Number (e.g. 'SC8125-04' or 'CB9001_12')
    disc_match = re.search(r'\b([A-Z]{2,4}\d{2,6})[-_](\d{1,3})\b', stem, re.IGNORECASE)
    if disc_match:
        disc_prefix = disc_match.group(1).upper()
        track_num = disc_match.group(2)
        disc_id = f"{disc_prefix}-{track_num}"
        # Look up manufacturer brand
        for code, brand_name in KARAOKE_BRANDS.items():
            if disc_prefix.startswith(code):
                brand = brand_name
                break
        # Remove disc code from stem
        stem = re.sub(r'\b[A-Z]{2,4}\d{2,6}[-_]\d{1,3}\b[\s\-_]*', '', stem, flags=re.IGNORECASE).strip()

    # 2. Strip YouTube 11-char Video IDs (e.g. '[4ahHWROn8M0]' or '(4ahHWROn8M0)')
    stem = re.sub(r'[\(\[\{]\s*[a-zA-Z0-9_-]{11}\s*[\)\]\}]', '', stem).strip()

    # 3. Strip Video Resolution & Format Tags
    stem = re.sub(r'[\(\[\{]\s*(?:1080p|720p|480p|360p|4k|2k|hd|hq|60fps|uhd)\s*[\)\]\}]', '', stem, flags=re.IGNORECASE).strip()

    # 4. Strip Karaoke / Lyric Video Noise Descriptors
    noise_patterns = [
        r'[\(\[\{]\s*(?:karaoke|instrumental|lyrics|karaoke version|sing-along|with lyrics|sing along)\s*[\)\]\}]',
        r'[\(\[\{]\s*(?:official lyric video|official karaoke|karafun|official audio|acoustic karaoke)\s*[\)\]\}]',
        r'[\(\[\{]\s*(?:lower key|higher key|piano cover|backing track|female key|male key|original key)\s*[\)\]\}]',
        r'[\(\[\{]\s*(?:lyrics video|color coded lyrics|easy lyrics|cc|no lead vocal)\s*[\)\]\}]',
        r'[\(\[\{]\s*video with lyrics[^\)\]\}]*[\)\]\}]',
        r',?\s*video with lyrics[^\,]*',
        r',?\s*instrumental version',
        r'\b(?:lyrics|karaoke|sing-along|instrumental)\b\s*$',
    ]
    for np in noise_patterns:
        stem = re.sub(np, '', stem, flags=re.IGNORECASE).strip()

    # 5. Strip Leading Track Numbers (e.g. '01 - ' or '12. ')
    stem = re.sub(r'^\d+[\s\.\-_]+', '', stem).strip()

    # 6. Check 'In The Style Of' / 'As Made Famous By' pattern
    artist = ""
    title = ""
    style_of_match = re.search(r'^(.*?)\s+(?:in\s+the\s+style\s+of|as\s+made\s+famous\s+by|tribute\s+to|originally\s+by)\s+(.*?)$', stem, re.IGNORECASE)
    if style_of_match:
        title = style_of_match.group(1).strip()
        artist = style_of_match.group(2).strip()
        # Clean leftover noise from artist
        artist = re.sub(r'[\(\[\{].*?[\)\]\}]', '', artist).strip()

    # 7. Delimiter Split to Separate Artist & Title (if not resolved by style_of)
    if not (artist and title):
        delimiters = [' - ', ' _ ', ' – ', ' — ', ' ~ ']
        for delim in delimiters:
            if delim in stem:
                parts = stem.split(delim, 1)
                artist = parts[0].strip()
                title = parts[1].strip()
                break

    # If no standard delimiter, try single hyphen if surrounded by spaces
    if not (artist and title) and '-' in stem:
        parts = stem.split('-', 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            artist = parts[0].strip()
            title = parts[1].strip()

    # 8. Check Parent Folder if still undivided
    if not (artist and title):
        title = stem
        if parent_folder and parent_folder.lower().strip() not in GENERIC_FOLDERS:
            # If parent folder is a known brand, keep artist as "Karaoke Track"
            if parent_folder.lower().strip() not in KNOWN_BRAND_FOLDERS:
                artist = parent_folder.strip()
            else:
                artist = "Karaoke Track"
        else:
            artist = "Karaoke Track"

    # 9. Apply Phonebook Inversion on Artist ('Cash, Johnny' -> 'Johnny Cash')
    artist = invert_phonebook_name(artist)

    # Clean leftover edge punctuation
    title = title.strip(" -_–—~[](),")
    artist = artist.strip(" -_–—~[](),")

    return {
        "artist": artist or "Karaoke Track",
        "title": title or stem,
        "brand": brand,
        "disc_id": disc_id,
        "track_num": track_num,
        "clean_stem": stem,
        "original_filename": filename,
    }
