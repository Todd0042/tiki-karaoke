# 🌴 Florida Tiki Karaoke Lounge & Neon Bar

A self-hosted, broadcast-grade Karaoke Party Server, DJ, and Archiver application styled after a vibrant Florida Tiki Bar with neon lights.

---

## ✨ Features

- **📱 Mobile Web App (Smartphones)**: Zero-install PWA. Guests scan a QR code on the TV screen over local Wi-Fi, register a nickname with a tropical avatar (🌺, 🦩, 🍹, 🌴, 🥥, 🦜, 🏄), search songs, pick custom keys, and queue requests.
- **⚡ Tiered Search Pipeline (Local-First $\rightarrow$ Online Fallback)**:
  - **Tier 1 (Instant Local Library)**: Sub-millisecond SQLite FTS5 full-text search across downloaded tracks with zero buffering and zero quota usage.
  - **Tier 2 (On-Demand YouTube Query)**: Automatically queries YouTube if not found locally, removing clutter (`[Sing King]`, `4K`, `Official`) and clustering versions (backing vocals, guide melody, key changes) into clean cards.
- **👑 Private Host DJ Remote & Dual QR System**:
  - **Public QR Code**: Displayed on TV for guests (`http://<ip>:8000/join`).
  - **Private Host QR Code**: Opened from the TV admin modal or terminal (`http://<ip>:8000/host?token=...`), turning the host's phone into the Master DJ remote with Skip, Pause, Live Pitch/EQ, Soundboard, and TV volume.
- **🎵 Real-Time Web Audio DSP (Pitch Shifting & 3-Band Parametric EQ)**:
  - Built-in Chris Wilson delay-line pitch shifter ($\pm 6$ semitones) that changes key without altering tempo or video sync.
  - Hardware-accelerated 3-band parametric EQ (Bass, Mid, Treble) and Web Audio peak limiter preventing audio distortion.
- **🔊 Automated Audio Loudness Normalization (EBU R128 / LUFS)**:
  - Every downloaded song is automatically normalized to **-16 LUFS** (-1.5 dBFS True Peak) using FFmpeg `loudnorm` in 1–2s without re-encoding video.
- **🌴 Broadcast-Grade Stage Intermission Display**:
  - Fullscreen TV display with 20-second circular countdown timer, Up Next tickers, and background intermission music (BGM) with auto-ducking.
  - Optional Virtual DJ TTS voice announcer (toggleable on/off for live human MCs).
  - Floating audience emoji reactions (🔥, 🍹, 🌴, 👏, 🌺, 🍻) sent live from crowd smartphones.
- **📥 Background Downloader & Local Archiver (`yt-dlp` + `ffmpeg`)**:
  - Asynchronously downloads played/queued tracks into `karaoke_library/tracks/[Artist]/[Title].mp4`.
  - Automatically exports standard `.m3u8` playlists for each singer (`karaoke_library/playlists_m3u/[Singer].m3u8`).
- **☁️ Google Account Playlist Sync (OAuth 2.0)**:
  - Automatically saves played tracks to dedicated playlists in the host's YouTube account (`Karaoke - [Singer Name]`) with deduplication.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.10+
- FFmpeg (installed and available on PATH)

### 2. Launching the Server
```bash
cd ~/Documents/GitHub/tiki-karaoke
./start.sh
```
Or directly:
```bash
./venv/bin/python main.py
```
By default, the server establishes a **permanent, fixed static URL** (`http://bore.pub:38480`) that **never changes across restarts** and requires zero AT&T router port forwarding or configuration.

### 3. Remote Access & URLs
- **Main TV Stage Display**: `http://bore.pub:38480/stage` (or local `http://192.168.1.189:8000/stage`)
- **Guest / Singer Mobile View**: `http://bore.pub:38480/join` (or local `http://192.168.1.189:8000/join`)
- **Host DJ Remote URL**: `http://bore.pub:38480/host?token=24bb8bedcc7c2342d099b69c912db6e0`
- **Host 4-Digit PIN**: `1234`

Options:
- `./start.sh`: Launches with the permanent static public URL (`bore.pub:38480`).
- `./start.sh --local-only`: Run strictly on the local Wi-Fi network without remote tunneling.
- `./start.sh --tunnel`: Use an ephemeral Cloudflare HTTPS tunnel if preferred.
- `--no-browser`: Do not auto-launch browser window on start.

### 3. Screen URLs
- **Main TV Stage Display**: `http://<your-local-ip>:8000/stage`
- **Guest / Singer Mobile View**: `http://<your-local-ip>:8000/join`
- **Host DJ Remote URL**: `http://<your-local-ip>:8000/host?token=<HOST_TOKEN>`
- **Host 4-Digit PIN**: `1234` (or configured via `TIKI_HOST_PIN` environment variable).

---

## 📁 Directory Structure

```
tiki-karaoke/
├── main.py                   # Server CLI entry point & terminal QR generator
├── app.py                    # FastAPI server, WebSockets hub & routes
├── database.py               # SQLite schema, WAL mode, FTS5 index, queue & history
├── search.py                 # Tiered search engine & RapidFuzz deduplicator
├── downloader.py             # Background yt-dlp downloader & FFmpeg loudnorm (-16 LUFS)
├── playlist_sync.py          # Google OAuth 2.0 & YouTube playlist sync
├── config.py                 # Paths, ports, and persistent secrets
├── static/
│   ├── css/tiki-theme.css    # Florida Tiki Bar neon theme & animations
│   ├── js/audio-dsp.js       # Web Audio EQ, Pitch Shifter & Peak Limiter
│   ├── js/host-player.js     # Chromium dual-player engine & stage intermission
│   ├── js/mobile-client.js   # Mobile client PWA logic & host controls
│   ├── sounds/               # Soundboard stems (airhorn, applause, rimshot, bird)
│   └── bgm/                  # Ambient background intermission music
├── templates/
│   ├── host.html             # TV / Main stage display template
│   └── mobile.html           # Smartphone web client template
├── data/
│   └── karaoke.db            # SQLite database & FTS5 index
└── karaoke_library/
    ├── tracks/               # Permanent local library of normalized MP4s
    └── playlists_m3u/        # Auto-generated M3U playlists per singer
```

---

## 🧪 Running Tests
```bash
./venv/bin/python test_system.py
./venv/bin/python test_api.py
```
