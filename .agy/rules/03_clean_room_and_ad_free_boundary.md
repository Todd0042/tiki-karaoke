# Rule: Clean-Room & Ad-Free Repository Boundary

## Objective
Preserve the absolute purity, ethical standing, and clean-room provenance of the public `Todd0042/tiki-karaoke` repository under the PolyForm Noncommercial 1.0.0 license.

## Protocol
1. **Zero Ad Code in Public Repository**:
   - The public repository `Todd0042/tiki-karaoke` must NEVER contain third-party ad networks, tracking scripts, ad-insertion logic, or interstitial paywalls.
   - Any experimental ad-supported features or monetization tests are strictly quarantined within the private repository `Todd0042/tiki-karaoke-ads`.
2. **Audio & Media Provenance**:
   - All bundled audio files (BGM tracks, soundboard effects, vinyl crackle) must remain 100% mathematically synthesized clean-room code (generated via `scripts/generate_bgm.py` and `scripts/generate_soundboard_audio.py`).
   - Never commit copyrighted audio samples, ripped soundboards, or unlicensed stock music to this repository.
3. **Platform Distribution Segregation**:
   - **GitHub Releases**: Contains ONLY the Linux AppImage (`TikiKaraoke-Pro-v*-x86_64.AppImage`) for Linux users and Arch AUR packaging. Pre-built Windows binaries are NEVER attached to GitHub Releases.
   - **Itch.io (Butler)**: Serves Windows builds (Installer and Portable ZIP) and Linux AppImage packages.
