# Rule: Packaging, Checksum & AUR Release Integrity

## Objective
Ensure that Arch User Repository (`tiki-karaoke-bin`), Linux AppImage, and Windows releases maintain exact version alignment and valid SHA256 checksums without breakage.

## Protocol
1. **Version Alignment**:
   - Whenever `APP_VERSION` is updated in `config.py`, the exact same version string must be updated in:
     - `packaging/aur/PKGBUILD` (`pkgver=...`, resetting `pkgrel=1`)
     - `packaging/aur/.SRCINFO` (`pkgver = ...`)
     - `build_appimage.sh` (`VERSION="${TIKI_VERSION:-...}"`)
     - `.github/workflows/build_releases.yml`
     - `packaging/windows/README_WINDOWS.txt`
2. **SHA256 Checksum Accuracy**:
   - Never commit placeholder or guessed SHA256 sums.
   - Whenever an AppImage release asset is rebuilt or re-tagged, compute its exact hash (`sha256sum`) and update `sha256sums` in `packaging/aur/PKGBUILD` and `packaging/aur/.SRCINFO`.
3. **Packaging Verification**:
   - Run `makepkg --printsrcinfo > .SRCINFO` to guarantee `.SRCINFO` exactly matches `PKGBUILD`.
   - Run `makepkg -sfc` locally or verify syntax before committing packaging changes.
