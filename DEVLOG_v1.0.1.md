# Tiki Karaoke v1.0.1 Devlog: Low-Latency Mic Mastery, Karaoke CDGs & Home Lounge Focus

**Release Date:** October 24, 2026  
**Version:** `1.0.1`  
**Platforms:** Windows (Portable ZIP & Inno Setup Installer), Linux (Universal x86_64 AppImage)

---

## 🌺 Welcome to Tiki Karaoke v1.0.1!

Following our initial `v1.0.0` release, we've focused entirely on refining the desktop experience across **Windows** and **Linux**. This release delivers substantial under-the-hood audio engineering: native low-latency microphone loopback, rock-solid hardware audio device enumeration, native Karaoke CD (`.cdg` / `.mp3+.cdg` / `.zip`) graphic decoding, and an updated licensing posture dedicated purely to personal, family, and home party entertainment.

Here is an in-depth look at everything included in `v1.0.1`.

---

## 🎤 1. Low-Latency Microphone Pipeline & Routing Overhaul

Getting live vocal audio from a microphone back out to speakers without disorienting latency has always been the hardest part of computer karaoke. In `v1.0.1`, we made major advancements across both OS backends and browser Web Audio routing:

### ⚡ Windows Native WASAPI Loopback (`WasapiAudioRouter`)
- Built a dedicated background audio loopback router utilizing Windows native **WASAPI** (via an isolated miniaudio helper).
- Bypasses browser-level Web Audio buffer bloat and shared audio engine latency, achieving sub-20ms round-trip vocal monitoring on standard USB mics, line-in inputs, and wireless receiver bases.
- Added strict process isolation and test hygiene guards: audio devices are never left open or held in an exclusive lock after host shutdown or test suites.

### 🐧 Linux Low-Latency Capture
- Optimized native ALSA, PulseAudio, and PipeWire capture pipelines within the containerized AppImage environment.
- Streamlined buffer negotiation so USB audio interfaces and wireless microphone dongles initialize instantly without sample rate mismatches or underruns.

### 🔍 Hardware Device Enumeration & GUID Matching Fix
- **The Issue**: In previous builds, when merging system-level audio devices with the browser's `navigator.mediaDevices.enumerateDevices()`, browser-generated device IDs and device GUIDs could collide or overwrite the native OS hardware IDs. This could cause the host player to bind to an unexpected default input or fail to open a selected USB microphone.
- **The Fix**: Overhauled `populateHostMicDevices()` to strictly preserve native OS hardware IDs while mapping descriptive browser labels cleanly. The host interface now accurately reflects your physical hardware inputs every time.

### 📊 Real-Time Native Router Telemetry & Visual Gating
- The **Host DJ Desk** and **Mobile Host Remote** now dynamically report the active audio routing mode:
  - 🟢 **Native Router Active**: Sub-20ms hardware monitoring in effect.
  - 🟡 **Browser Web Audio Fallback**: Displayed when running across unaccelerated setups, complete with latency advisory tooltips (warning against high-latency Bluetooth headsets or TV HDMI audio processing delay).
  - 🎛️ **Live Hardware VU & Master Mic Controls**: Gain, volume sliders, warm room reverb, and automatic between-song vocal ducking are synchronized instantly across desktop and mobile screens.

---

## 📀 2. Native Karaoke CD Support (MP3+CDG & ZIP Archives)

Many karaoke fans have extensive physical disc collections converted into computer files. `v1.0.1` adds full, native support for classic Karaoke CD formats:

- **Supported Formats**:
  - Uncompressed `.mp3` + `.cdg` paired files
  - Direct compressed `.zip` CDG archive playback (no need to manually extract hundreds of songs!)
- **Zero-Plugin Canvas Renderer (`cdg-renderer.js`)**:
  - Custom client-side graphics parser renders authentic 16-color CD+G subtitle tiles, sweeps, and borders at 300x216 resolution, scaled dynamically with crisp nearest-neighbor smoothing for modern 4K/1080p TVs.
  - Full synchronization with Web Audio playback and ±6 semitone real-time key/pitch transposition.

---

## 🏡 3. Focus on Personal, Home Lounge & Private Party Use

With `v1.0.1`, we have officially removed commercial venue licensing, enterprise multi-room modules, and paid performance provisions from our core documentation, in-app guides, and installer scripts.

- **Non-Commercial License**: Tiki Karaoke Lounge is licensed strictly for personal, hobbyist, and private non-commercial recreational use under the **PolyForm Noncommercial License 1.0.0**.
- **Streamlined `/legal` Guide**: The in-app media guide has been completely refreshed to focus on personal home libraries—explaining how to rip and organize your own CD+G discs and where to buy digital backing tracks (such as Karaoke-Version and Sunfly) for private home enjoyment.
- **No Subscriptions, 100% Offline**: Turn on your Wi-Fi router, plug in your TV, and host your party in your living room, cabin, or backyard without requiring an internet connection or any subscription fees.

---

## 📦 4. Release Downloads & Packages

| Package | Description | Status |
| :--- | :--- | :--- |
| **`TikiKaraokeSetup-v1.0.1.exe`** | Turnkey Windows Installer for Windows 10 & 11 (64-bit) with clean Start Menu shortcuts and full uninstaller. | Ready for build via `build_windows.ps1` |
| **`TikiKaraoke-Windows-v1.0.1-Portable.zip`** | Portable zero-install edition. Extract to a USB drive or external SSD and host from any PC. | Ready for packaging |
| **`TikiKaraoke-v1.0.1-x86_64.AppImage`** | Standalone universal Linux AppImage. Runs out of the box on Ubuntu, Debian, Fedora, Arch, and Steam Deck / SteamOS. | Built & Verified in `dist/` |

---

*Grab your microphone, pour a tropical drink, and enjoy the party! 🍹🌺*
