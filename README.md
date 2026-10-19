# 🌴 Tiki Karaoke Lounge & Neon Bar

[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux%20x86__64-orange.svg)]()
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-green.svg)]()
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI%20%2B%20WebSockets-teal.svg)]()

> A modern, self-hosted, broadcast-grade Karaoke Party Server and Host DJ engine styled after a neon tropical tiki bar. Built specifically for Linux living rooms, party hosts, and audio enthusiasts.

<p align="center">
  <img src="screenshots/player_window_with_song_playing_clean.png" alt="Tiki Karaoke Stage Display" width="100%">
</p>

---

## 📸 Interface Showcase

### 📺 Main Stage Display (Connected to TV via HDMI)
The stage runs borderless in kiosk mode with synchronized lyrics, video playback, automated intermission countdowns, and real-time on-screen audience emoji reactions:

<p align="center">
  <img src="screenshots/player_window_with_song_playing_clean.png" alt="Stage View" width="90%">
</p>

### 👑 Host DJ Master Command Desk
Full dashboard for the party host: real-time pitch controls, venue audio sync calibration, filler background music crate, soundboard effects, PipeWire/PulseAudio multi-display routing, and singer roster management:

<p align="center">
  <img src="screenshots/host-controls-page_clean.png" alt="Host DJ Controls" width="90%">
</p>

### 📱 Guest Singer Experience (Zero App Installation)
Guests simply scan the TV screen's QR code with their phone cameras to search songs, adjust their vocal pitch, and track their position in line:

| 🔍 Instant Search & YouTube | 🎵 Pitch Shift & Duets | 📜 Stage Line-Up & Hype Cheer |
| :---: | :---: | :---: |
| <img src="screenshots/mobile_search_clean.png" width="280"> | <img src="screenshots/mobile_pitch_modal_clean.png" width="280"> | <img src="screenshots/mobile_lineup_clean.png" width="280"> |
| *Instant local cache + YouTube search* | *±3 semitone pitch shift & duet partner* | *Live queue rotation & real-time emoji cheer* |

### ⚙️ Initial Setup Wizard
Interactive web configuration wizard with hardware multi-monitor detection, audio card selection, and modular SSL tunnel options:

<p align="center">
  <img src="screenshots/Initial-Setup-Wizard_clean.png" alt="Initial Setup Wizard" width="90%">
</p>

---

## 🍹 Why Tiki Karaoke?

Most Linux karaoke software was abandoned over a decade ago—requiring complex local text files, dated UIs, or manual window shuffling. 

**Tiki Karaoke is designed for modern house parties:**
* **Zero App Installation for Guests**: Singers point their phone cameras at the TV screen's QR code. They can instantly browse songs, search YouTube, and queue tracks with their own nickname and tropical avatar.
* **Autonomous Stage Display**: Connect an HDMI cable from your laptop or mini-PC to your TV. The stage runs borderless in kiosk mode with automated countdown intermissions, background video music, and live on-screen audience emoji reactions.
* **Private Host DJ Remote**: The host controls pitch shifting, live EQ, intermission timers, queue reordering, and soundboard effects directly from their phone or secondary monitor.
* **Hardware-Accelerated Web Audio DSP**: Real-time key/pitch shifting ($\pm 6$ semitones) without altering playback speed or video sync, 3-band parametric EQ, and venue A/V latency calibration.

---

## 🚀 60-Second Quick Start (Linux)

### 1. Prerequisites
* **Python 3.10 or newer**
* **FFmpeg** (for YouTube audio extraction and loudness normalization)
  ```bash
  # Ubuntu / Debian / Linux Mint:
  sudo apt update && sudo apt install -y ffmpeg python3-venv

  # Arch Linux / Manjaro:
  sudo pacman -S ffmpeg python

  # Fedora / RHEL:
  sudo dnf install -y ffmpeg python3
  ```

### 2. Clone & Install
```bash
git clone https://github.com/your-username/tiki-karaoke.git
cd tiki-karaoke
./install.sh
```
*The installer automatically creates the Python virtual environment, installs dependencies, and creates a desktop application launcher.*

### 3. Start the Lounge
```bash
./start.sh
```
*(Or launch **Tiki Karaoke** directly from your Linux Application Menu!)*

---

## 🖥️ Screen URLs & Remote Access (🔒 Trusted SSL by Default)

When launched with internet connectivity, Tiki Karaoke automatically establishes an **encrypted Cloudflare Quick Tunnel** (`https://*.trycloudflare.com`), providing guests with an instant **trusted 🔒 HTTPS green padlock** on their mobile phones with zero browser security warnings.

| Screen | URL | Purpose |
| :--- | :--- | :--- |
| **📺 Main Stage Display** | `https://<tunnel-id>.trycloudflare.com/stage` *(or `http://<ip>:8000/stage`)* | Fullscreen TV display showing video lyrics, countdowns, and emoji hype. |
| **📱 Guest Singer Join** | `https://<tunnel-id>.trycloudflare.com/join` *(or `http://<ip>:8000/join`)* | Mobile web app for singers to search songs and join the queue. |
| **👑 Host DJ Remote** | `https://<tunnel-id>.trycloudflare.com/host?token=...` | Protected DJ dashboard with pitch shifting, queue controls, and settings. |

* **Default Host PIN**: `1234` (configurable via `./setup.sh` or web setup).
* **CLI Options**:
  * `./start.sh`: Standard start with automatic Cloudflare 🔒 HTTPS and port collision fallback.
  * `./start.sh --local-only`: Restricts traffic strictly to your local Wi-Fi LAN (`http://<ip>:8000`).
  * `./start.sh --bore`: Use legacy static port HTTP tunnel (`http://bore.pub:38480`).
  * `./start.sh --no-browser`: Headless mode (does not auto-open browser windows).
  * `./start.sh --stop`: Gracefully terminates all background services.

### 🌐 Modular Remote Access & Domain Options
Run `./setup.sh` or visit `/setup` anytime to configure your preferred remote access provider:
1. **Cloudflare Quick Tunnel (Default)**: Zero setup, auto HTTPS, changing session URL.
2. **Cloudflare Named Tunnel (Custom Domain)**: Connect your own domain (e.g. `https://sing.mybar.com`) for permanent branded lounge access.
3. **Tailscale Funnel**: Free permanent `https://*.ts.net` HTTPS URL without owning a domain or opening router ports.
4. **DuckDNS + Caddy**: Free subdomain with automated Let's Encrypt certificates (requires router ports 80/443).
5. **Local Wi-Fi Only**: Offline mode for cabins, off-grid parties, or private local networks.
6. **Bore**: Static unencrypted port fallback.

---

## ✨ Feature Highlights

### ⚡ Tiered Search Engine
* **Instant Local Cache**: Zero-latency SQLite FTS5 search across downloaded video tracks.
* **On-Demand YouTube Fallback**: Automatically queries YouTube for karaoke versions, stripping clickbait tags (`[Sing King]`, `Official 4K`) and clustering versions (backing vocals, guide melody, instrumental).

### 🎛️ Real-Time Venue A/V Sync Calibration
* Compensates for video processing lag on smart TVs and Bluetooth speaker delays directly from the Host Remote:
  * **Direct Audio**: `0 ms` (wired studio monitors or headphones)
  * **Smart TV Lag**: `+80 ms` (HDMI input processing delay)
  * **Projector**: `+140 ms` (home theater projector delay)
  * **Bluetooth Speaker**: `-150 ms` (wireless audio latency compensation)
  * Live `[ -10ms ]` / `[ +10ms ]` fine-tuning nudges during live playback.

### 🔔 30-Second Non-Blocking On-Deck Alert
* Exactly 30 seconds before a singer's song begins, their phone receives an alert pill notifying them to grab a microphone.
* **Zero Show Interruption**: The stage flow never pauses waiting for singer confirmation; intermission countdowns proceed autonomously.

### 🔊 EBU R128 Loudness Normalization
* Every downloaded song is automatically analyzed and normalized to **-16 LUFS** (-1.5 dBFS True Peak) using FFmpeg `loudnorm`, eliminating jarring volume differences between quiet 80s ballads and loud modern pop tracks.

---

## 🛠️ Troubleshooting & FAQ

#### ⚠️ "Port 8000 is already in use"
Tiki Karaoke automatically detects occupied ports and seamlessly shifts to `8001`, `8002`, etc., updating QR codes dynamically. To stop previous instances manually, run:
```bash
./start.sh --stop
```

#### ⚠️ "FFmpeg is missing on host machine"
If `ffmpeg` is not found, YouTube downloads will be paused. Install FFmpeg using your package manager (`sudo apt install ffmpeg`), then restart `./start.sh`.

#### 🔇 Audio playing through laptop instead of TV HDMI
1. Run `./setup.sh` to launch the interactive configuration wizard.
2. Under **Stage Audio Output Device**, select your TV's HDMI audio sink from the detected PulseAudio / PipeWire devices.

#### 📱 Mobile devices cannot connect over Local Wi-Fi
If your Linux host has an active firewall (`ufw`), incoming connections from guest phones on your local Wi-Fi may be rejected. Allow incoming traffic on port 8000:
```bash
sudo ufw allow 8000/tcp comment "Tiki Karaoke"
```

---

## 🧪 Running the Test Suite

Tiki Karaoke includes an automated test suite covering API endpoints, WebSocket broadcasting, queue ordering, and latency calibration:

```bash
./venv/bin/python test_system.py
```

---

## 📜 License

This project is licensed under the **PolyForm Noncommercial License 1.0.0** ([LICENSE](LICENSE)).

* **Permitted**: Free personal use, hobbyist use, private house parties, educational use, and beta testing/modification on Linux.
* **Prohibited**: Commercial exploitation, charging entry/subscriptions, or resale without written commercial licensing from the author.
