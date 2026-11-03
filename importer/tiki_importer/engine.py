import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Set, Tuple

from .cleaner import clean_track_name
from .enricher import MetadataEnricher
from .db_adapter import TikiDatabaseAdapter
from .compuhost import CompuHostDatabase

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".mpg", ".mpeg"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
CDG_EXTS = {".cdg", ".zip"}


def probe_duration(file_path: str) -> int:
    """Probes media duration in seconds via ffprobe, falling back to 210s."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 210
    try:
        cmd = [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
        val = res.stdout.strip()
        if val:
            return max(1, int(float(val)))
    except Exception:
        pass
    return 210


def collect_media_files(folder_path: str, recursive: bool = True) -> List[Dict[str, Any]]:
    """
    Scans a folder and pairs CDG+MP3 files, ZIP archives, and video files.
    Returns a list of track items ready for processing.
    """
    root = Path(folder_path)
    if not root.is_dir():
        return []

    iterator = root.rglob("*") if recursive else root.glob("*")
    files = [p for p in iterator if p.is_file()]

    media_items: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()

    # Pass 1: Identify CDG pairs and zip archives
    cdg_by_stem: Dict[Tuple[str, str], Path] = {}
    audio_by_stem: Dict[Tuple[str, str], Path] = {}

    for f in files:
        ext = f.suffix.lower()
        parent_str = str(f.parent.resolve()).lower()
        stem_lower = f.stem.lower()
        key = (parent_str, stem_lower)

        if ext == ".cdg":
            cdg_by_stem[key] = f
        elif ext in AUDIO_EXTS:
            audio_by_stem[key] = f
        elif ext == ".zip":
            # Direct CDG zip package
            seen_paths.add(str(f.resolve()).lower())
            media_items.append({
                "type": "cdg",
                "audio_path": str(f.resolve()),
                "cdg_path": str(f.resolve()),
                "filename": f.name,
                "path": str(f.resolve()),
            })

    # Pair up cdg + audio
    for key, cdg_file in cdg_by_stem.items():
        audio_file = audio_by_stem.get(key)
        if audio_file:
            seen_paths.add(str(audio_file.resolve()).lower())
            seen_paths.add(str(cdg_file.resolve()).lower())
            media_items.append({
                "type": "cdg",
                "audio_path": str(audio_file.resolve()),
                "cdg_path": str(cdg_file.resolve()),
                "filename": audio_file.name,
                "path": str(audio_file.resolve()),
            })
        else:
            # Standalone cdg without matched audio (or embedded)
            seen_paths.add(str(cdg_file.resolve()).lower())
            media_items.append({
                "type": "cdg",
                "audio_path": str(cdg_file.resolve()),
                "cdg_path": str(cdg_file.resolve()),
                "filename": cdg_file.name,
                "path": str(cdg_file.resolve()),
            })

    # Pass 2: Video files and remaining audio
    for f in files:
        resolved_str = str(f.resolve()).lower()
        if resolved_str in seen_paths:
            continue

        ext = f.suffix.lower()
        if ext in VIDEO_EXTS:
            seen_paths.add(resolved_str)
            media_items.append({
                "type": "video",
                "audio_path": str(f.resolve()),
                "cdg_path": "",
                "filename": f.name,
                "path": str(f.resolve()),
            })

    return media_items


class TikiImportEngine:
    """
    Main orchestration engine for scanning, catalog matching,
    web metadata enrichment, and database insertion.
    """
    def __init__(
        self,
        db_path: str,
        compuhost_path: Optional[str] = None,
        enable_enrichment: bool = True
    ):
        self.db = TikiDatabaseAdapter(db_path)
        self.existing_paths = self.db.get_existing_paths()
        self.compuhost = CompuHostDatabase(compuhost_path) if compuhost_path else None
        self.enricher = MetadataEnricher() if enable_enrichment else None

    def scan_and_import(
        self,
        folder_path: str,
        dry_run: bool = True,
        recursive: bool = True,
        progress_callback: Optional[Callable[[int, int, Dict[str, Any]], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes a scan over folder_path.
        Reports progress via progress_callback(index, total, record).
        Returns a list of all processed records.
        """
        media_items = collect_media_files(folder_path, recursive=recursive)
        total = len(media_items)
        results: List[Dict[str, Any]] = []

        for idx, item in enumerate(media_items, start=1):
            file_path = item["path"]
            filename = item["filename"]
            media_type = item["type"]
            cdg_path = item["cdg_path"]

            # 1. Check existing in database
            is_duplicate = str(Path(file_path).resolve()).lower() in self.existing_paths

            # 2. Clean filename with heuristics and folder context
            parent_name = item.get("parent_folder") or Path(file_path).parent.name
            cleaned = clean_track_name(filename, parent_folder=parent_name)
            artist = cleaned["artist"]
            title = cleaned["title"]
            brand = cleaned["brand"]
            disc_id = cleaned["disc_id"]
            track_num = cleaned["track_num"]
            release_year = 0
            genre = ""
            status = "new"

            if is_duplicate:
                status = "already_in_db"

            # 3. Cross-reference CompuHost Database (.tps, .ciqbackup, .csv) if loaded
            if self.compuhost and self.compuhost.total_records > 0:
                match = self.compuhost.lookup(
                    file_path=file_path,
                    filename=filename,
                    artist=artist,
                    title=title,
                )
                if match:
                    if match.get("artist"):
                        artist = match["artist"]
                    if match.get("title"):
                        title = match["title"]
                    if match.get("brand"):
                        brand = match["brand"]
                    if match.get("year"):
                        release_year = match["year"]
                    if match.get("genre"):
                        genre = match["genre"]
                    if status == "new":
                        status = "compuhost_match"

            # 4. Web Enrichment via iTunes / Cache (if enabled)
            if self.enricher:
                enriched = self.enricher.enrich(artist, title)
                if enriched.get("enriched"):
                    artist = enriched["artist"]
                    title = enriched["title"]
                    if not release_year and enriched.get("release_year"):
                        release_year = enriched["release_year"]
                    if not genre and enriched.get("genre"):
                        genre = enriched["genre"]
                    if status == "new":
                        status = "web_enriched"
                    elif enriched.get("swapped"):
                        status = "swapped_corrected"

            # 5. Database Insertion if not dry_run
            track_id = None
            if not dry_run:
                duration = probe_duration(file_path)
                channel = brand if brand else "Imported"
                track_id = self.db.insert_track(
                    file_path=file_path,
                    artist=artist,
                    title=title,
                    duration=duration,
                    release_year=release_year,
                    genre=genre,
                    channel=channel,
                    is_sing_along=False,
                    media_type=media_type,
                    cdg_path=cdg_path
                )
                if status == "new":
                    status = "imported"
                elif status == "already_in_db":
                    status = "updated"

            record = {
                "id": track_id,
                "filename": filename,
                "path": file_path,
                "artist": artist,
                "title": title,
                "release_year": release_year,
                "genre": genre,
                "brand": brand,
                "media_type": media_type,
                "status": status,
                "dry_run": dry_run,
            }
            results.append(record)

            if progress_callback:
                progress_callback(idx, total, record)

        return results

    def migrate_compuhost(
        self,
        compuhost_path: str,
        remap_from: str = "",
        remap_to: str = "",
        skip_missing: bool = True,
        dry_run: bool = True,
        progress_callback: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Directly migrates tracks from a CompuHost database (SongList.tps or .ciqbackup)
        into Tiki Karaoke without requiring a raw directory scan.
        """
        import sys
        path_obj = Path(compuhost_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"CompuHost database not found: {compuhost_path}")

        ext = path_obj.suffix.lower()
        if ext in {".tps", ".ciqbackup"}:
            from .tps_reader import parse_songlist_tps
            raw_records = parse_songlist_tps(path_obj)
        else:
            from .compuhost import CompuHostDatabase
            ch_db = CompuHostDatabase(str(path_obj))
            raw_records = list(ch_db.by_path.values()) or list(ch_db.by_artist_title.values())

        total = len(raw_records)
        results: List[Dict[str, Any]] = []

        for idx, r in enumerate(raw_records, start=1):
            raw_artist = r.get("artist", "").strip()
            title = r.get("title", "").strip()
            raw_path = r.get("path", "").strip()
            filename = r.get("filename", "")

            from .cleaner import invert_phonebook_name
            artist = invert_phonebook_name(raw_artist)

            # Apply path remapping if configured
            target_path = raw_path
            if remap_from and remap_to and raw_path:
                norm_from = remap_from.replace("\\", "/").rstrip("/").lower()
                norm_p = target_path.replace("\\", "/")
                if norm_p.lower().startswith(norm_from):
                    norm_to = remap_to.replace("\\", "/").rstrip("/")
                    target_path = norm_to + norm_p[len(norm_from):]
                    if sys.platform == "win32":
                        target_path = target_path.replace("/", "\\")
                    else:
                        target_path = target_path.replace("\\", "/")
            elif sys.platform != "win32" and target_path:
                target_path = target_path.replace("\\", "/")

            # Check file existence
            resolved_p = Path(target_path)
            file_exists = False
            try:
                file_exists = resolved_p.is_file()
            except Exception:
                file_exists = False

            # If not found directly, check cross-platform fallback
            if not file_exists and sys.platform != "win32" and ":" in target_path:
                parts = target_path.replace("\\", "/").split("/")
                fname = parts[-1]
                def_tracks = Path.home() / "Music" / "Karaoke"
                if (def_tracks / fname).is_file():
                    target_path = str((def_tracks / fname).resolve())
                    file_exists = True

            media_type = "video"
            cdg_path = ""
            target_ext = resolved_p.suffix.lower()
            if target_ext in VIDEO_EXTS:
                media_type = "video"
            elif target_ext in CDG_EXTS:
                media_type = "cdg"
                cdg_path = target_path
            elif target_ext in AUDIO_EXTS:
                sibling_cdg = resolved_p.with_suffix(".cdg")
                if sibling_cdg.is_file():
                    media_type = "cdg"
                    cdg_path = str(sibling_cdg.resolve())
                else:
                    media_type = "audio"

            is_duplicate = False
            if file_exists:
                is_duplicate = str(resolved_p.resolve()).lower() in self.existing_paths

            status = "ready"
            if not file_exists:
                status = "missing_file"
            elif is_duplicate:
                status = "already_in_db"

            release_year = r.get("year", 0) or 0
            genre = r.get("genre", "") or ""
            brand = r.get("brand", "") or ""

            # Web enrichment if enabled
            if self.enricher and (status != "missing_file" or not skip_missing):
                enriched = self.enricher.enrich(artist, title)
                if enriched.get("enriched"):
                    artist = enriched["artist"]
                    title = enriched["title"]
                    if not release_year and enriched.get("release_year"):
                        release_year = enriched["release_year"]
                    if not genre and enriched.get("genre"):
                        genre = enriched["genre"]
                    if status == "ready":
                        status = "web_enriched"

            # Insert if not dry run and file exists (or not skip_missing)
            track_id = None
            if not dry_run and (file_exists or not skip_missing):
                duration = probe_duration(target_path) if file_exists else 210
                channel = brand if brand else "CompuHost"
                track_id = self.db.insert_track(
                    file_path=target_path,
                    artist=artist,
                    title=title,
                    duration=duration,
                    release_year=release_year,
                    genre=genre,
                    channel=channel,
                    is_sing_along=False,
                    media_type=media_type,
                    cdg_path=cdg_path,
                )
                if status in {"ready", "web_enriched"}:
                    status = "imported"
                elif status == "already_in_db":
                    status = "updated"

            rec = {
                "id": track_id,
                "filename": filename or resolved_p.name,
                "path": target_path,
                "raw_path": raw_path,
                "artist": artist,
                "title": title,
                "release_year": release_year,
                "genre": genre,
                "brand": brand,
                "media_type": media_type,
                "status": status,
                "file_exists": file_exists,
                "dry_run": dry_run,
            }
            results.append(rec)

            if progress_callback:
                progress_callback(idx, total, rec)

        return results
