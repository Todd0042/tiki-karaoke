# Tiki Karaoke — Manual Review Issues

Items flagged during the automated test run that require human verification.
Re-run `python -m pytest tests/test_comprehensive.py -v` to refresh this file.

## No issues found — all automated checks passed.

---

## Always-manual checks (not automatable):

- [ ] Launch `main.py` and verify the Control Hub desktop window appears
- [ ] Open the Host Desk in a browser and confirm all tabs render correctly
- [ ] Open the TV Stage view — verify the queue ticker and idle screen display
- [ ] Test the Singer join flow on a mobile device (scan QR code)
- [ ] Verify CDG+G Ripper window opens from Smart Library Importer (button)
- [ ] Verify CompuHost Migration Wizard opens from Smart Library Importer
- [ ] Test an actual YouTube karaoke download end-to-end
- [ ] Verify BGM filler video plays during intermission on the stage display
- [ ] Test host microphone monitoring in native + web-audio modes
- [ ] Test CD+G disc detection (requires physical drive and disc)
- [ ] Test CompuHost database import (requires real SongList.tps file)
- [ ] Verify Cloudflare/Bore tunnel connects and public URL works
- [ ] Test multi-monitor display routing (requires 2 monitors)
- [ ] Verify yt-dlp update path works without breaking running downloads
