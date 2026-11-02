#!/usr/bin/env python3
"""
Downloads precompiled 64-bit Windows standalone binaries for Tiki Karaoke packaging:
- ffmpeg.exe & ffprobe.exe (from Gyan / BtbN release)
- cloudflared.exe (from Cloudflare official release)
- bore.exe (from ekzhang/bore release)
"""
import os
import sys
import shutil
import zipfile
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BIN_DIR = BASE_DIR / "bin"

BINARIES = {
    "cloudflared": {
        "filename": "cloudflared.exe",
        "url": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
        "is_zip": False,
    },
    "bore": {
        "filename": "bore.exe",
        "url": "https://github.com/ekzhang/bore/releases/download/v0.5.2/bore-v0.5.2-x86_64-pc-windows-msvc.zip",
        "is_zip": True,
        "zip_exe_name": "bore.exe",
    },
    "ffmpeg": {
        "filename": "ffmpeg.exe",
        "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "is_zip": True,
        "zip_exe_name": "ffmpeg.exe",
        "also_extract": ["ffprobe.exe"]
    }
}

def download_file(url: str, dest_path: Path):
    print(f"📥 Downloading: {url} -> {dest_path.name}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest_path.with_suffix(dest_path.suffix + ".download")
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TikiKaraokePackager/1.0"}
    req = urllib.request.Request(url, headers=headers)
    
    with urllib.request.urlopen(req) as response, open(temp_dest, "wb") as out_file:
        total_size = int(response.info().get("Content-Length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024  # 1 MB chunks
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                percent = (downloaded / total_size) * 100
                mb_down = downloaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                sys.stdout.write(f"\r   Progress: {percent:5.1f}% ({mb_down:5.1f} MB / {mb_total:5.1f} MB)")
                sys.stdout.flush()
    print("\n   Download complete.")
    temp_dest.rename(dest_path)

def extract_from_zip(zip_path: Path, target_names: list, dest_dir: Path):
    print(f"📦 Extracting {target_names} from {zip_path.name}...")
    with zipfile.ZipFile(zip_path, 'r') as z:
        for member in z.infolist():
            member_base = Path(member.filename).name.lower()
            for target in target_names:
                if member_base == target.lower():
                    dest_file = dest_dir / target
                    print(f"   -> Extracting {member.filename} to {dest_file.name}")
                    with z.open(member) as source, open(dest_file, "wb") as target_f:
                        shutil.copyfileobj(source, target_f)

def fetch_all_binaries(output_dir: Path = DEFAULT_BIN_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"🌴 Staging Windows 64-bit Binaries into: {output_dir}\n")

    for key, info in BINARIES.items():
        target_file = output_dir / info["filename"]
        if target_file.exists():
            print(f"✅ {info['filename']} already exists in {output_dir.name}/. Skipping.")
            continue

        if info["is_zip"]:
            zip_dest = output_dir / f"{key}_temp.zip"
            try:
                download_file(info["url"], zip_dest)
                targets = [info["zip_exe_name"]]
                if "also_extract" in info:
                    targets.extend(info["also_extract"])
                extract_from_zip(zip_dest, targets, output_dir)
            finally:
                if zip_dest.exists():
                    zip_dest.unlink()
        else:
            download_file(info["url"], target_file)

    print("\n🎉 Windows 64-bit binary staging complete:")
    for f in output_dir.glob("*.exe"):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"   • {f.name:<16} ({size_mb:.2f} MB)")

if __name__ == "__main__":
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_BIN_DIR
    fetch_all_binaries(out)
