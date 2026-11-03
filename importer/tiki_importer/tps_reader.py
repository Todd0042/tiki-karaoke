"""
Tiki Karaoke — Clarion TopSpeed (.tps) & CompuHost CatalogIQ (.ciqbackup) Parser
Pure Python, zero-dependency reverse-engineered reader.
"""
import struct
import re
from pathlib import Path
from typing import Dict, Any, List, Optional


def de_rle(compressed: bytes) -> bytes:
    """Decompresses TopSpeed Run-Length-Encoded (RLE) page data."""
    out = bytearray()
    idx = 0
    n = len(compressed)

    while idx < n:
        skip = compressed[idx]
        idx += 1
        if skip == 0:
            break
        if skip > 0x7F:
            if idx >= n:
                break
            msb = compressed[idx]
            idx += 1
            lsb = skip & 0x7F
            shift = 0x80 * (msb & 0x01)
            skip = ((msb << 7) & 0x00FF00) + lsb + shift

        out.extend(compressed[idx:idx + skip])
        idx += skip

        if idx < n - 1:
            to_repeat = compressed[idx - 1]
            repeats_minus_one = compressed[idx]
            idx += 1
            if repeats_minus_one > 0x7F:
                if idx >= n:
                    break
                msb = compressed[idx]
                idx += 1
                lsb = repeats_minus_one & 0x7F
                shift = 0x80 * (msb & 0x01)
                repeats_minus_one = ((msb << 7) & 0x00FF00) + lsb + shift

            out.extend([to_repeat] * repeats_minus_one)

    return bytes(out)


def parse_songlist_tps(file_path_or_bytes) -> List[Dict[str, Any]]:
    """
    Parses a CompuHost SongList.tps file (or raw bytes or .ciqbackup) and returns all song records.
    Returns: list of dicts with keys: 'artist', 'title', 'path', 'filename'
    """
    if isinstance(file_path_or_bytes, (str, Path)):
        with open(file_path_or_bytes, "rb") as f:
            data = f.read()
    else:
        data = bytes(file_path_or_bytes)

    # Check for .ciqbackup wrapper
    if data.startswith(b"CHIQ-RECOVERY"):
        # Find embedded SongList.tps
        for m in re.finditer(rb'tOpS', data):
            if m.start() >= 14:
                start = m.start() - 14
                addr, hdr_size, len1, len2 = struct.unpack('<IHII', data[start:start+14])
                if addr == 0 and hdr_size == 0x200 and len1 > 0 and len1 == len2:
                    data = data[start:start+len1]
                    break

    if len(data) < 0x200 or data[14:18] != b"tOpS":
        return []

    songs: List[Dict[str, Any]] = []
    pos = 0x200

    while pos < len(data):
        if pos + 13 > len(data):
            break
        page_addr, page_size = struct.unpack('<IH', data[pos:pos+6])
        if page_size < 13 or pos + page_size > len(data):
            pos += 0x100
            continue

        page_uncomp, _, rec_count, flags = struct.unpack('<HHHB', data[pos+6:pos+13])
        page_raw = data[pos+13:pos+page_size]

        if flags == 0x00 and page_size != page_uncomp:
            try:
                uncomp = de_rle(page_raw)
            except Exception:
                uncomp = page_raw
        else:
            uncomp = page_raw

        # Parse records inside page
        p_pos = 0
        prev_data = b""
        prev_rec_len = 0
        prev_hdr_len = 0
        rec_idx = 0

        while p_pos < len(uncomp):
            r_flags = uncomp[p_pos]
            p_pos += 1

            if rec_idx == 0:
                if (r_flags & 0xC0) != 0xC0:
                    break
                r_len, h_len = struct.unpack('<HH', uncomp[p_pos:p_pos+4])
                p_pos += 4
                r_data = uncomp[p_pos:p_pos+r_len]
                p_pos += r_len
            else:
                r_len = struct.unpack('<H', uncomp[p_pos:p_pos+2])[0] if (r_flags & 0x80) else prev_rec_len
                if r_flags & 0x80: p_pos += 2
                h_len = struct.unpack('<H', uncomp[p_pos:p_pos+2])[0] if (r_flags & 0x40) else prev_hdr_len
                if r_flags & 0x40: p_pos += 2
                copy = r_flags & 0x3F
                r_data = prev_data[:copy] + uncomp[p_pos:p_pos + (r_len - copy)]
                p_pos += (r_len - copy)

            prev_data = r_data
            prev_rec_len = r_len
            prev_hdr_len = h_len
            rec_idx += 1

            hdr = r_data[:h_len]
            body = r_data[h_len:]

            # Look for song data records: body length >= 200 and contains path separator
            if len(body) >= 200 and (b":\\" in body or b":/" in body):
                title = body[0x04:0x40].decode('latin1', errors='ignore').strip()
                artist = body[0x40:0x7c].decode('latin1', errors='ignore').strip()

                # Extract path
                path = ""
                if len(body) >= 0xd1 + 60 and (b":\\" in body[0xd1:0xd1+260] or b":/" in body[0xd1:0xd1+260]):
                    raw_p = body[0xd1:0xd1+260].decode('latin1', errors='ignore').strip()
                    clean_p = ""
                    for ch in raw_p:
                        if ord(ch) < 32 or ch == '\x00':
                            break
                        clean_p += ch
                    path = clean_p.strip()
                else:
                    m = re.search(rb'[a-zA-Z]:[\\/][^\x00\r\n\t]+', body)
                    if m:
                        path = m.group().decode('latin1', errors='ignore').strip()

                if path and (title or artist):
                    filename = path.replace("\\", "/").split("/")[-1]
                    songs.append({
                        "artist": artist,
                        "title": title,
                        "path": path,
                        "filename": filename,
                    })

        # Next page aligned to 0x100
        pos += page_size
        if pos % 0x100 != 0:
            pos = ((pos // 0x100) + 1) * 0x100

    return songs
