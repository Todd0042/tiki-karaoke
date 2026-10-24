# Tiki Karaoke v1.0.1 Devlog: Smooth Streams, Real-Time Progress, and Multiplatform Harmony

**Release Date:** October 24, 2026  
**Version:** `1.0.1`  
**Platforms:** Android (including Android Auto), Linux (x86_64 AppImage), Windows (Portable ZIP & Inno Setup Installer)

---

## 🌺 Overview: What's New in v1.0.1

In this release, we tackled one of the most frustrating pain points when using **Tiki Karaoke** in on-the-go or mobile environments: **the frozen 0% download progress bar on requested karaoke tracks.**

Alongside this crucial fix, we achieved full version parity across the ecosystem, packaging **`v1.0.1`** across **Android**, **Linux**, and **Windows**.

---

## 🚀 Key Improvements & Highlights

### 1. Robust Download Engine & Real-Time Progress Parsing (Android & Android Auto)
- **The Issue**: Previous builds relied on rigid ETA string format matching (`ETA mm:ss`) from `youtubedl-android`'s process stream. When downloading YouTube DASH video/audio fragments over cellular, mobile hotspots, or vehicle Wi-Fi, yt-dlp often returns variable timing or `ETA Unknown`. This caused regex extraction to fail, falling back to `-1.0f` which clamped to `0%` indefinitely—leaving singers and hosts wondering if anything was happening.
- **The Fix**: 
  - Overhauled `TrackDownloadManager.kt` to match percent progress lines directly (`Regex("""(\d+(?:\.\d+)?)%""")`), regardless of whether yt-dlp outputs an ETA, speed, or chunk count.
  - Implemented human-readable phase telemetry so users always know the exact pipeline stage:
    - 🔍 *Extracting video stream info...*
    - ⏳ *Buffering 480p media stream...*
    - 📥 *Downloading 480p: X%*
    - 🎛️ *Merging audio & video with FFmpeg...*
  - Added process isolation using a tracked `processId = "dl_$videoId"`.
  - Added full **Download Cancellation** support: singers and hosts can now cancel an active download midway through via `POST /api/download/cancel`, cleanly terminating orphaned yt-dlp and FFmpeg background tasks.
  - Added fallback status polling (`GET /api/download/status?video_id={id}`) in `mobile-client.js` to guarantee progress updates even if a WebSocket message drops during network handoffs.
  - Added auto-normalization for webm/mkv formats to standard `.mp4` for flawless HTML5 stage video playback.

### 2. Multiplatform Version Synchronization (`1.0.1`)
All distribution bundles and build configurations have been synchronized to version `1.0.1`:

- **Android**:
  - Binary: `TikiKaraoke-v1.0.1-AndroidAuto.apk`
  - Integrated with foreground service notifications, car audio routing, and dual-display car stage rendering.
- **Linux**:
  - Target: `TikiKaraoke-v1.0.1-x86_64.AppImage`
  - Fully self-contained container build with bundled Python 3.11, FastAPI, FFmpeg, and native audio routing.
- **Windows**:
  - Targets: `TikiKaraokeSetup-v1.0.1.exe` and `TikiKaraoke-Windows-v1.0.1-Portable.zip`
  - Updated PowerShell and batch automation (`build_windows.ps1`, `build_windows.bat`) and Inno Setup configuration (`installer.iss`) with matching file version properties and metadata.

---

## 📦 Artifacts & Deliverables

| Platform | Deliverable | Location |
| :--- | :--- | :--- |
| **Android / Android Auto** | `TikiKaraoke-v1.0.1-AndroidAuto.apk` | `tiki-karaoke-android/TikiKaraoke-v1.0.1-AndroidAuto.apk` |
| **Linux (Standalone)** | `TikiKaraoke-v1.0.1-x86_64.AppImage` | `tiki-karaoke/dist/TikiKaraoke-v1.0.1-x86_64.AppImage` |
| **Windows Build Suite** | `installer.iss`, `build_windows.ps1` | `tiki-karaoke-windows/` |

---

## 🔮 Next Roadmap Milestones
- **Mic Audio Drift Compensation**: USB-mic-in to HDMI-out synchronization over multi-hour karaoke sessions.
- **Latency Calibration & Monitoring Modes**: Built-in hardware-direct / native / browser monitoring gating and clap-test calibration.
- **Cross-Room Stage Remote Synchronization**: Expanded mobile host controls for secondary stages.
