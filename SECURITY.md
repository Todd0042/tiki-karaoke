# Security Policy

## Supported Versions

Security updates and dependency patches are actively maintained for the latest release line of **Tiki Karaoke Lounge**:

| Version | Supported          |
| ------- | ------------------ |
| 1.1.x   | :white_check_mark: |
| < 1.1.0 | :x:                |

## Architectural Security Guarantees

* **Local-First & Self-Hosted**: Operates 100% locally on your network without mandatory third-party accounts, external trackers, or proprietary cloud telemetry.
* **Network & Host Isolation**: Local server binds to user-specified host/port, protected by Host DJ PIN authentication for sensitive operations (queue reordering, track skips, loudness recalibration, system reboot/shutdown).
* **Deterministic Subprocess Execution**: External tools (`ffmpeg`, `ffprobe`, `yt-dlp`, CD+G rippers) are executed with array parameters (`shell=False`) and rigorous filename/path validation to prevent command injection.
* **Deterministic Dependency Pinning**: Dependencies in `requirements.txt` and GitHub Actions workflows are pinned to explicit, immutable versions and commit SHAs.

## Reporting a Vulnerability

If you discover a potential security vulnerability, please report it privately via email to **todd.0042.git@gmail.com** or through [GitHub Private Vulnerability Reporting](https://github.com/Todd0042/tiki-karaoke/security/advisories/new) rather than filing a public issue. Security reports will receive an acknowledgment within 48 hours.
