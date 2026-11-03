import csv
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

from .cleaner import invert_phonebook_name, KARAOKE_BRANDS
from .tps_reader import parse_songlist_tps


class CompuHostDatabase:
    """
    Unified reader for CompuHost database files (.tps, .ciqbackup) and legacy exports (.csv).
    Indexes songs by path, filename, and artist/title for instant cross-referencing.
    """
    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path
        self.by_path: Dict[str, Dict[str, Any]] = {}
        self.by_filename: Dict[str, Dict[str, Any]] = {}
        self.by_artist_title: Dict[str, Dict[str, Any]] = {}
        self.total_records = 0

        if file_path and Path(file_path).exists():
            self.load(file_path)

    def load(self, file_path: str):
        path = Path(file_path)
        if not path.exists():
            return

        self.by_path.clear()
        self.by_filename.clear()
        self.by_artist_title.clear()
        self.total_records = 0

        # Check if file is TopSpeed .tps or .ciqbackup
        ext = path.suffix.lower()
        if ext in {".tps", ".ciqbackup"}:
            self._load_tps(path)
        else:
            self._load_csv(path)

    def _load_tps(self, path: Path):
        try:
            records = parse_songlist_tps(path)
            for r in records:
                artist = invert_phonebook_name(r.get("artist", ""))
                title = r.get("title", "")
                raw_path = r.get("path", "")
                filename = r.get("filename", "")

                rec = {
                    "artist": artist,
                    "title": title,
                    "path": raw_path,
                    "filename": filename,
                    "brand": "",
                    "year": 0,
                    "genre": "",
                }

                self.total_records += 1

                # 1. Index by normalized path
                if raw_path:
                    norm_path = raw_path.replace("\\", "/").lower()
                    self.by_path[norm_path] = rec

                # 2. Index by filename
                if filename:
                    stem = Path(filename).stem.lower()
                    self.by_filename[stem] = rec
                    self.by_filename[filename.lower()] = rec

                # 3. Index by artist + title
                if artist and title:
                    self.by_artist_title[f"{artist.lower()}:::{title.lower()}"] = rec
        except Exception as e:
            print(f"[CompuHost] Error parsing TPS file {path}: {e}")

    def _load_csv(self, path: Path):
        content = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                continue
        if not content:
            return

        lines = content.splitlines()
        reader = csv.reader(lines)
        try:
            header = next(reader)
        except StopIteration:
            return

        for row in reader:
            if not row or not any(row):
                continue
            # Basic fallback CSV indexing
            raw_artist = row[0].strip() if len(row) > 0 else ""
            raw_title = row[1].strip() if len(row) > 1 else ""
            if raw_artist and raw_title:
                clean_a = invert_phonebook_name(raw_artist)
                rec = {
                    "artist": clean_a,
                    "title": raw_title,
                    "path": "",
                    "filename": "",
                    "brand": "",
                    "year": 0,
                    "genre": "",
                }
                self.total_records += 1
                self.by_artist_title[f"{clean_a.lower()}:::{raw_title.lower()}"] = rec

    def lookup(
        self,
        file_path: str = "",
        filename: str = "",
        artist: str = "",
        title: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Finds a matching CompuHost record via path, filename stem, or artist/title."""
        # 1. Exact Path match
        if file_path:
            norm_p = file_path.replace("\\", "/").lower()
            if norm_p in self.by_path:
                return self.by_path[norm_p]

        # 2. Filename match
        if filename:
            stem = Path(filename.replace("\\", "/").split("/")[-1]).stem.lower()
            if stem in self.by_filename:
                return self.by_filename[stem]
            fname_lower = filename.replace("\\", "/").split("/")[-1].lower()
            if fname_lower in self.by_filename:
                return self.by_filename[fname_lower]

        # 3. Artist + Title match
        if artist and title:
            at_key = f"{artist.lower()}:::{title.lower()}"
            if at_key in self.by_artist_title:
                return self.by_artist_title[at_key]

        return None
