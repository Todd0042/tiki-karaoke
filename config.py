import os
import sys
import json
import secrets
import shutil
import subprocess
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else APP_DIR
else:
    APP_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = APP_DIR

BASE_DIR = BUNDLE_DIR
STATIC_DIR = BUNDLE_DIR / "static"
TEMPLATES_DIR = BUNDLE_DIR / "templates"
APP_VERSION = os.getenv("TIKI_VERSION", "").strip() or "1.1.3"

def get_user_data_root() -> Path:
    """Returns safe persistent user data root across installed, portable, and AppImage modes."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            test_file = APP_DIR / ".write_test"
            test_file.touch()
            test_file.unlink()
            return APP_DIR
        except (PermissionError, OSError):
            appdata = os.getenv("APPDATA")
            if appdata:
                return Path(appdata) / "TikiKaraoke"
    elif getattr(sys, "frozen", False):
        # On Linux AppImage / PyInstaller: check if directory is writable (AppImage squashfs is read-only)
        try:
            test_file = APP_DIR / ".write_test"
            test_file.touch()
            test_file.unlink()
            return APP_DIR
        except (PermissionError, OSError):
            config_home = os.getenv("XDG_CONFIG_HOME")
            base = Path(config_home) / "TikiKaraoke" if config_home else Path.home() / ".config" / "TikiKaraoke"
            base.mkdir(parents=True, exist_ok=True)
            return base
    return APP_DIR

CONFIG_FILE = get_user_data_root() / "config.json"

def get_bundled_binary_path(binary_name: str) -> Path:
    """
    Returns the resolved Path to a bundled binary (e.g. cloudflared, bore, ffmpeg).
    Supports PyInstaller bundle directory (_MEIPASS), AppImage APPDIR, and Windows .exe extensions.
    """
    ext = ".exe" if sys.platform == "win32" else ""
    full_name = f"{binary_name}{ext}" if not binary_name.endswith(ext) else binary_name

    # 1. Check PyInstaller runtime temp dir if packaged (both _internal/bin and parent usr/bin/bin)
    if hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        for cand in (
            meipass / "bin" / full_name,
            meipass.parent / "bin" / full_name,
            meipass / full_name,
            meipass.parent / full_name,
        ):
            if cand.exists():
                return cand

    # 1b. Check AppImage APPDIR environment variable if set
    appdir_env = os.environ.get("APPDIR", "").strip()
    if appdir_env:
        appdir_path = Path(appdir_env)
        for cand in (
            appdir_path / "usr" / "bin" / "bin" / full_name,
            appdir_path / "usr" / "bin" / "_internal" / "bin" / full_name,
            appdir_path / "usr" / "bin" / full_name,
        ):
            if cand.exists():
                return cand

    # 2. Check project / application bin directory
    for cand in (
        APP_DIR / "bin" / full_name,
        APP_DIR / "_internal" / "bin" / full_name,
        APP_DIR / full_name,
        BASE_DIR / "bin" / full_name,
        BASE_DIR / "_internal" / "bin" / full_name,
    ):
        if cand.exists():
            return cand

    # 2b. Check staging packaging bin directory (including sibling windows repo)
    pkg_bin = BASE_DIR / "packaging" / "windows" / "bin" / full_name
    if pkg_bin.exists():
        return pkg_bin

    sibling_win_bin = BASE_DIR.parent / "tiki-karaoke-windows" / "bin" / full_name
    if sibling_win_bin.exists():
        return sibling_win_bin

    # 3. Fallback to system PATH lookup
    sys_which = shutil.which(full_name) or shutil.which(binary_name)
    if sys_which:
        return Path(sys_which)

    return BASE_DIR / "bin" / full_name


def find_system_browser(name: str) -> Optional[str]:
    """Finds browser executable across Linux (native, snap, flatpak) and Windows systems."""
    name_clean = name.lower().strip()

    # 1. Direct which search
    direct_candidates = [name_clean]
    alias_map = {
        "firefox": ["firefox", "firefox-esr", "firefox-bin"],
        "chrome": ["google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"],
        "google-chrome": ["google-chrome", "google-chrome-stable", "chrome"],
        "google-chrome-stable": ["google-chrome-stable", "google-chrome", "chrome"],
        "edge": ["microsoft-edge", "microsoft-edge-stable", "msedge"],
        "microsoft-edge": ["microsoft-edge", "microsoft-edge-stable", "msedge"],
        "microsoft-edge-stable": ["microsoft-edge-stable", "microsoft-edge", "msedge"],
        "msedge": ["msedge", "microsoft-edge", "microsoft-edge-stable"],
        "brave": ["brave-browser", "brave"],
        "brave-browser": ["brave-browser", "brave"],
        "chromium": ["chromium", "chromium-browser"],
        "chromium-browser": ["chromium-browser", "chromium"]
    }
    for alias in alias_map.get(name_clean, [name_clean]):
        sys_path = shutil.which(alias)
        if sys_path:
            return sys_path

    # 2. Linux Snap & Flatpak checks
    if sys.platform != "win32":
        snap_candidates = {
            "firefox": ["/snap/bin/firefox", "/var/lib/snapd/snap/bin/firefox"],
            "chrome": ["/snap/bin/chromium", "/var/lib/snapd/snap/bin/chromium"],
            "google-chrome": ["/snap/bin/chromium", "/var/lib/snapd/snap/bin/chromium"],
            "chromium": ["/snap/bin/chromium", "/var/lib/snapd/snap/bin/chromium"],
            "brave": ["/snap/bin/brave", "/var/lib/snapd/snap/bin/brave"],
            "brave-browser": ["/snap/bin/brave", "/var/lib/snapd/snap/bin/brave"],
            "edge": ["/snap/bin/edge", "/var/lib/snapd/snap/bin/edge"],
        }
        for p_str in snap_candidates.get(name_clean, []):
            if Path(p_str).exists() and os.access(p_str, os.X_OK):
                return p_str

        flatpak_app_ids = {
            "firefox": "org.mozilla.firefox",
            "chrome": "com.google.Chrome",
            "google-chrome": "com.google.Chrome",
            "google-chrome-stable": "com.google.Chrome",
            "edge": "com.microsoft.Edge",
            "microsoft-edge": "com.microsoft.Edge",
            "microsoft-edge-stable": "com.microsoft.Edge",
            "msedge": "com.microsoft.Edge",
            "brave": "com.brave.Browser",
            "brave-browser": "com.brave.Browser",
            "chromium": "org.chromium.Chromium",
            "chromium-browser": "org.chromium.Chromium"
        }
        app_id = flatpak_app_ids.get(name_clean)
        if app_id:
            flatpak_paths = [
                Path(f"/var/lib/flatpak/exports/bin/{app_id}"),
                Path.home() / f".local/share/flatpak/exports/bin/{app_id}"
            ]
            for fp in flatpak_paths:
                if fp.exists() and os.access(str(fp), os.X_OK):
                    return str(fp)

    # 3. Windows system paths
    if sys.platform == "win32":
        prog_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        prog_64 = os.environ.get("ProgramFiles", r"C:\Program Files")
        local_app = os.environ.get("LocalAppData", "")

        candidates = {
            "microsoft-edge": [
                Path(prog_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(prog_64) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ],
            "msedge": [
                Path(prog_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(prog_64) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ],
            "edge": [
                Path(prog_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(prog_64) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ],
            "google-chrome": [
                Path(prog_64) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(prog_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe",
            ],
            "chrome": [
                Path(prog_64) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(prog_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe",
            ],
            "firefox": [
                Path(prog_64) / "Mozilla Firefox" / "firefox.exe",
                Path(prog_x86) / "Mozilla Firefox" / "firefox.exe",
            ],
            "brave-browser": [
                Path(prog_64) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
                Path(local_app) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            ],
            "brave": [
                Path(prog_64) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
                Path(local_app) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            ],
            "chromium": [
                Path(prog_64) / "Chromium" / "Application" / "chrome.exe",
                Path(local_app) / "Chromium" / "Application" / "chrome.exe",
            ]
        }
        for path in candidates.get(name_clean, []):
            if path.exists():
                return str(path)

    return None


def get_available_browsers() -> List[Dict[str, Any]]:
    """
    Detects installed web browsers across Linux and Windows with recommendations
    based on audio/video autoplay compatibility and seamless user experience.
    """
    browser_defs = [
        {
            "id": "firefox",
            "name": "Mozilla Firefox",
            "recommended": True,
            "badge": "⭐ Top Pick (Zero Autoplay Hassle)",
            "description": "Best experience: audio and video play automatically with zero clicks or initial gesture blocks.",
            "search_keys": ["firefox"]
        },
        {
            "id": "chrome",
            "name": "Google Chrome",
            "recommended": False,
            "badge": "⚠️ Autoplay Notice",
            "description": "Chromium security requires clicking 'Play' once when the first song begins.",
            "search_keys": ["google-chrome", "chrome"]
        },
        {
            "id": "edge",
            "name": "Microsoft Edge",
            "recommended": False,
            "badge": "⚠️ Autoplay Notice",
            "description": "Chromium security requires clicking 'Play' once when the first song begins.",
            "search_keys": ["microsoft-edge", "msedge"]
        },
        {
            "id": "brave",
            "name": "Brave Browser",
            "recommended": False,
            "badge": "⚠️ Autoplay Notice",
            "description": "Chromium security requires clicking 'Play' once when the first song begins.",
            "search_keys": ["brave-browser", "brave"]
        },
        {
            "id": "chromium",
            "name": "Chromium",
            "recommended": False,
            "badge": "⚠️ Autoplay Notice",
            "description": "Chromium security requires clicking 'Play' once when the first song begins.",
            "search_keys": ["chromium", "chromium-browser"]
        }
    ]

    results: List[Dict[str, Any]] = []

    for b in browser_defs:
        resolved_path = None
        for key in b["search_keys"]:
            resolved_path = find_system_browser(key)
            if resolved_path:
                break

        results.append({
            "id": b["id"],
            "name": b["name"],
            "path": resolved_path or "",
            "available": bool(resolved_path),
            "recommended": b["recommended"] if resolved_path else False,
            "badge": b["badge"],
            "description": b["description"]
        })

    # System default browser is always available as fallback
    results.append({
        "id": "default",
        "name": "System Default Browser",
        "path": "OS default (via standard launcher)",
        "available": True,
        "recommended": False,
        "badge": "Standard",
        "description": "Opens windows using whichever browser is configured as your operating system default."
    })

    return results


def open_url_in_preferred_browser(url: str) -> bool:
    """
    Opens a URL in the user's preferred browser (or auto-detected Firefox/Chrome/OS default).
    """
    import webbrowser
    if os.environ.get("TIKI_TESTING") or os.environ.get("TIKI_DATA_DIR"):
        return True

    cfg = load_config()
    pref = str(cfg.get("preferred_browser", "auto")).lower().strip()

    if pref in ("default", "os", "webbrowser"):
        try:
            return webbrowser.open(url)
        except Exception:
            return False

    target_bin = None
    if pref != "auto":
        target_bin = find_system_browser(pref)
    else:
        # In auto mode, prioritize Firefox if installed
        target_bin = find_system_browser("firefox")

    if target_bin:
        try:
            subprocess.Popen([target_bin, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"[Browser] Could not launch {target_bin}: {e}")

    try:
        return webbrowser.open(url)
    except Exception:
        return False


def get_system_drives() -> List[str]:
    """Returns a list of root directory paths for available drives on Windows, or ['/'] on Unix."""
    if sys.platform != "win32":
        return ["/"]
    import string
    drives = []
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                p = f"{letter}:\\"
                if os.path.exists(p):
                    drives.append(p)
            bitmask >>= 1
    except Exception:
        for letter in string.ascii_uppercase:
            p = f"{letter}:\\"
            if os.path.exists(p):
                drives.append(p)
    return drives or ["C:\\"]


DEFAULT_CONFIG = {
    "configured": False,
    "bar_name": "The Tiki Hut Karaoke Lounge",
    "host_pin": "1234",
    "port": 8000,
    "remote_mode": "cloudflare_quick",
    "hotspot_enabled": False,
    "hotspot_ssid": "",
    "hotspot_password": "",
    "remote_port": 38480,
    "public_host": "",
    "cloudflare_token": "",
    "custom_domain": "",
    "tailscale_domain": "",
    "duckdns_domain": "",
    "duckdns_token": "",
    "target_lufs": -16.0,
    "intermission_seconds": 15,
    "tts_enabled": False,
    "bgm_enabled": True,
    "bgm_volume": 0.25,
    "google_client_id": "",
    "google_client_secret": "",
    "library_dir": "",
    "data_dir": "",
    "recordings_dir": "",
    "stage_display": 0,
    "stage_display_name": "Primary Display",
    "stage_display_x": 0,
    "stage_display_y": 0,
    "stage_display_width": 1920,
    "stage_display_height": 1080,
    "stage_display_enabled": True,
    "kiosk_mode": False,
    "preferred_browser": "auto",
    "audio_output_device": "default",
    "audio_output_name": "System Default Audio Output",
    "audio_input_device": "default",
    "audio_input_name": "System Default Microphone / Input",
    "host_display_enabled": False,
    "host_display": 1,
    "host_display_name": "Secondary Display",
    "host_display_x": 0,
    "host_display_y": 0,
    "host_display_width": 1920,
    "host_display_height": 1080,
    "host_kiosk_mode": False,
    "video_quality_profile": "pro",
    "marquee_scroll_speed": 85,
    "mic_monitoring_mode": "native"
}

def get_available_displays() -> List[Dict[str, Any]]:
    """Returns a list of detected physical display monitors."""
    monitors = []
    # 1. Try xrandr on Linux
    if shutil.which("xrandr"):
        try:
            res = subprocess.run(["xrandr", "--listmonitors"], capture_output=True, text=True, timeout=2)
            lines = res.stdout.strip().split("\n")
            if len(lines) > 1:
                for line in lines[1:]:
                    m = re.search(r'^\s*(\d+):\s*(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)\s+(\S+)', line)
                    if m:
                        idx, flags, w, h, x, y, name = m.groups()
                        is_prim = "*" in flags
                        monitors.append({
                            "id": int(idx),
                            "name": name,
                            "label": f"Monitor {idx}: {name} [{w}x{h}]" + (" ★ Primary" if is_prim else ""),
                            "is_primary": is_prim,
                            "width": int(w),
                            "height": int(h),
                            "x": int(x),
                            "y": int(y)
                        })
        except Exception as e:
            print(f"[Display Detection] xrandr error: {e}")

    # Try enriching with monitor model names from GDK
    try:
        py_bin = sys.executable or shutil.which("python3") or "/usr/bin/python3"
        cmd = [py_bin, "-c", "import gi; gi.require_version('Gdk', '3.0'); from gi.repository import Gdk; Gdk.init([]); d = Gdk.Display.get_default(); print([(i, d.get_monitor(i).get_model() or '') for i in range(d.get_n_monitors())])"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
        if res.returncode == 0:
            import ast
            models = dict(ast.literal_eval(res.stdout.strip()))
            for m in monitors:
                if m["id"] in models and models[m["id"]]:
                    model_str = models[m["id"]].strip()
                    m["name"] = f"{m['name']} ({model_str})"
                    prim_str = " ★ Primary" if m["is_primary"] else ""
                    m["label"] = f"Monitor {m['id']}: {m['name']} [{m['width']}x{m['height']}]{prim_str}"
    except Exception:
        pass

    # 2. Windows display detection via user32.EnumDisplayMonitors (Zero external dependencies)
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            CCHDEVICENAME = 32

            class MONITORINFOEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * CCHDEVICENAME)
                ]

            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

            MonitorEnumProc = ctypes.WINFUNCTYPE(
                wintypes.BOOL,
                wintypes.HMONITOR,
                wintypes.HDC,
                ctypes.POINTER(wintypes.RECT),
                wintypes.LPARAM
            )

            raw_monitors = []

            def _enum_callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                info = MONITORINFOEXW()
                info.cbSize = ctypes.sizeof(MONITORINFOEXW)
                if user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
                    raw_monitors.append(info)
                return True

            cb = MonitorEnumProc(_enum_callback)
            user32.EnumDisplayMonitors(None, None, cb, 0)

            class DISPLAY_DEVICEW(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128),
                    ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128),
                    ("DeviceKey", wintypes.WCHAR * 128)
                ]

            for idx, mon in enumerate(raw_monitors):
                is_prim = bool(mon.dwFlags & 1)  # MONITORINFOF_PRIMARY = 1
                w = mon.rcMonitor.right - mon.rcMonitor.left
                h = mon.rcMonitor.bottom - mon.rcMonitor.top
                dev_name = mon.szDevice or f"Display {idx}"
                friendly_name = ""

                try:
                    disp_dev = DISPLAY_DEVICEW()
                    disp_dev.cb = ctypes.sizeof(DISPLAY_DEVICEW)
                    if user32.EnumDisplayDevicesW(mon.szDevice, 0, ctypes.byref(disp_dev), 0):
                        # Query monitor attached to this adapter
                        mon_dev = DISPLAY_DEVICEW()
                        mon_dev.cb = ctypes.sizeof(DISPLAY_DEVICEW)
                        if user32.EnumDisplayDevicesW(disp_dev.DeviceName, 0, ctypes.byref(mon_dev), 0):
                            friendly_name = mon_dev.DeviceString.strip()
                        if not friendly_name:
                            friendly_name = disp_dev.DeviceString.strip()
                except Exception:
                    pass

                display_title = f"{friendly_name} ({dev_name})" if friendly_name and friendly_name != dev_name else dev_name
                label = f"Monitor {idx}: {display_title} [{w}x{h}]"
                if is_prim:
                    label += " ★ Primary"

                monitors.append({
                    "id": idx,
                    "name": friendly_name or dev_name,
                    "device": dev_name,
                    "label": label,
                    "is_primary": is_prim,
                    "width": int(w),
                    "height": int(h),
                    "x": int(mon.rcMonitor.left),
                    "y": int(mon.rcMonitor.top)
                })
        except Exception as e:
            print(f"[Display Detection] Windows EnumDisplayMonitors error: {e}")

    # Fallback to primary default if no monitors found
    if not monitors:
        monitors.append({
            "id": 0,
            "name": "Default Display",
            "label": "Monitor 0: Default Display (Primary)",
            "is_primary": True,
            "width": 1920,
            "height": 1080,
            "x": 0,
            "y": 0
        })
    return monitors

def _windows_mic_router_devices() -> Optional[Dict[str, Any]]:
    """Query the bundled tiki-mic-router helper for WASAPI endpoints (Windows only).

    Returns {"playback": [...], "capture": [...]} or None if the helper is
    unavailable. Single source of truth: the same names the helper lists are
    the names it accepts as device specs for loopback start.
    """
    if sys.platform != "win32":
        return None
    try:
        helper = get_bundled_binary_path("tiki-mic-router")
        if not helper or not Path(helper).exists():
            return None
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            [str(helper), "--list-json"],
            capture_output=True, text=True, timeout=5,
            creationflags=creationflags
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip().splitlines()[-1])
            if isinstance(data.get("playback"), list) and isinstance(data.get("capture"), list):
                return data
    except Exception as e:
        print(f"[Audio Detection] tiki-mic-router --list-json error: {e}")
    return None

def get_available_audio_sinks() -> List[Dict[str, Any]]:
    """Returns detected system audio output devices."""
    devices = [{
        "id": "default",
        "name": "default",
        "description": "System Default Audio Output",
        "is_default": True
    }]

    # Windows: enumerate WASAPI endpoints via the tiki-mic-router helper
    if sys.platform == "win32":
        helper_data = _windows_mic_router_devices()
        if helper_data:
            for dev in helper_data["playback"]:
                name = str(dev.get("name", "")).strip()
                if not name or name in [d["id"] for d in devices]:
                    continue
                devices.append({
                    "id": name,
                    "name": name,
                    "description": name,
                    "is_default": bool(dev.get("is_default", False))
                })
        return devices

    # 1. Try pactl on Linux (PipeWire / PulseAudio)
    if shutil.which("pactl"):
        try:
            res = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True, timeout=2)
            sink_blocks = res.stdout.split("Sink #")
            for block in sink_blocks[1:]:
                name_match = re.search(r"Name:\s*(\S+)", block)
                desc_match = re.search(r"Description:\s*([^\n\r]+)", block)
                if name_match:
                    sink_name = name_match.group(1).strip()
                    sink_desc = desc_match.group(1).strip() if desc_match else sink_name
                    if sink_name not in [d["id"] for d in devices]:
                        devices.append({
                            "id": sink_name,
                            "name": sink_name,
                            "description": sink_desc,
                            "is_default": False
                        })
        except Exception as e:
            print(f"[Audio Detection] pactl error: {e}")

    return devices

def get_available_audio_sources() -> List[Dict[str, Any]]:
    """Returns detected system audio input devices (microphones, line-in, USB receivers)."""
    devices = [{
        "id": "default",
        "name": "default",
        "description": "System Default Microphone / Input",
        "is_default": True
    }]

    # Windows: enumerate WASAPI endpoints via the tiki-mic-router helper
    if sys.platform == "win32":
        helper_data = _windows_mic_router_devices()
        if helper_data:
            for dev in helper_data["capture"]:
                name = str(dev.get("name", "")).strip()
                # Exclude loopback/monitor endpoints (speaker captures, not mics)
                if not name or name.lower().startswith("monitor of"):
                    continue
                if name in [d["id"] for d in devices]:
                    continue
                devices.append({
                    "id": name,
                    "name": name,
                    "description": name,
                    "is_default": bool(dev.get("is_default", False))
                })
        return devices

    # 1. Try pactl on Linux (PipeWire / PulseAudio)
    if shutil.which("pactl"):
        try:
            res = subprocess.run(["pactl", "list", "sources"], capture_output=True, text=True, timeout=2)
            source_blocks = res.stdout.split("Source #")
            for block in source_blocks[1:]:
                name_match = re.search(r"Name:\s*(\S+)", block)
                desc_match = re.search(r"Description:\s*([^\n\r]+)", block)
                if name_match:
                    src_name = name_match.group(1).strip()
                    # Exclude monitor sources (these are loopbacks of speakers/headphones)
                    if src_name.endswith(".monitor"):
                        continue
                    src_desc = desc_match.group(1).strip() if desc_match else src_name
                    if src_name not in [d["id"] for d in devices]:
                        devices.append({
                            "id": src_name,
                            "name": src_name,
                            "description": src_desc,
                            "is_default": False
                        })
        except Exception as e:
            print(f"[Audio Input Detection] pactl error: {e}")

    return devices

def get_config_path() -> Path:
    root = get_user_data_root()
    cfg_file = root / "config.json"
    if cfg_file.exists():
        return cfg_file
    data_cfg = root / "data" / "config.json"
    if data_cfg.exists():
        return data_cfg
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    return cfg_file

def load_config() -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    cfg_path = get_config_path()
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    loaded = json.loads(content)
                    cfg.update(loaded)
        except Exception as e:
            print(f"[Config] Error reading {cfg_path}: {e}")
    return cfg

def get_default_library_dir() -> Path:
    return (Path.home() / "Music" / "Karaoke" / "Tiki").resolve()

def get_default_data_dir() -> Path:
    return (Path.home() / "Music" / "Karaoke" / "Tiki" / "db").resolve()

def get_library_dir() -> Path:
    cfg = load_config()
    p = os.getenv("TIKI_LIBRARY_DIR", "").strip() or cfg.get("library_dir", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return get_default_library_dir()

def get_tracks_dir() -> Path:
    return get_library_dir() / "tracks"

def get_playlists_dir() -> Path:
    return get_library_dir() / "playlists_m3u"

def get_filler_dir() -> Path:
    return get_library_dir() / "filler"

def get_sing_along_dir() -> Path:
    return get_library_dir() / "sing_along"

def get_default_recordings_dir() -> Path:
    return (get_library_dir() / "performance_recordings").resolve()

def get_recordings_dir() -> Path:
    cfg = load_config()
    p = os.getenv("TIKI_RECORDINGS_DIR", "").strip() or cfg.get("recordings_dir", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return get_default_recordings_dir()

def get_data_dir() -> Path:
    cfg = load_config()
    p = os.getenv("TIKI_DATA_DIR", "").strip() or cfg.get("data_dir", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return get_default_data_dir()

def get_db_path() -> Path:
    return get_data_dir() / "karaoke.db"

def get_secret_file() -> Path:
    return get_data_dir() / "host_token.txt"

def ensure_storage_dirs():
    try:
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        get_tracks_dir().mkdir(parents=True, exist_ok=True)
        get_playlists_dir().mkdir(parents=True, exist_ok=True)
        get_filler_dir().mkdir(parents=True, exist_ok=True)
        get_sing_along_dir().mkdir(parents=True, exist_ok=True)
        rec_dir = get_recordings_dir()
        rec_dir.mkdir(parents=True, exist_ok=True)

        # Legacy recordings directory migration safeguard:
        legacy_rec_dir = (get_library_dir() / "recordings").resolve()
        if legacy_rec_dir.exists() and legacy_rec_dir.is_dir() and legacy_rec_dir != rec_dir:
            try:
                for item in legacy_rec_dir.iterdir():
                    target_item = rec_dir / item.name
                    if not target_item.exists():
                        shutil.move(str(item), str(target_item))
                if not any(legacy_rec_dir.iterdir()):
                    legacy_rec_dir.rmdir()
            except Exception as e:
                print(f"[Storage] Notice: Legacy recordings migration note: {e}")

        # Legacy data migration safeguard:
        legacy_data = (get_user_data_root() / "data").resolve()
        if legacy_data != data_dir:
            legacy_db = legacy_data / "karaoke.db"
            target_db = data_dir / "karaoke.db"
            if legacy_db.exists() and not target_db.exists():
                try:
                    shutil.copy2(legacy_db, target_db)
                    print(f"[Config] Seamlessly migrated legacy database from {legacy_db} -> {target_db}")
                except Exception as mig_err:
                    print(f"[Config] Note: Could not copy legacy database: {mig_err}")

            legacy_token = legacy_data / "host_token.txt"
            target_token = data_dir / "host_token.txt"
            if legacy_token.exists() and not target_token.exists():
                try:
                    shutil.copy2(legacy_token, target_token)
                except Exception:
                    pass
    except Exception as e:
        print(f"[Config] Warning: could not ensure storage directories: {e}")

# Initialize directories
ensure_storage_dirs()

def get_host_token() -> str:
    sec_file = get_secret_file()
    if sec_file.exists():
        return sec_file.read_text().strip()
    # Check fallback legacy path
    legacy = BASE_DIR / "data" / "host_token.txt"
    if legacy.exists() and legacy != sec_file:
        token = legacy.read_text().strip()
        try:
            sec_file.parent.mkdir(parents=True, exist_ok=True)
            sec_file.write_text(token)
            return token
        except Exception:
            return token

    token = secrets.token_hex(16)
    try:
        sec_file.parent.mkdir(parents=True, exist_ok=True)
        sec_file.write_text(token)
    except Exception:
        pass
    return token

def refresh_globals():
    global DATA_DIR, LIBRARY_DIR, TRACKS_DIR, PLAYLISTS_DIR, DB_PATH, SECRET_FILE, HOST_TOKEN
    global HOST_PIN, PORT, PUBLIC_HOST, TARGET_LUFS, DEFAULT_INTERMISSION_SECONDS, REMOTE_MODE, REMOTE_PORT, BAR_NAME, VIDEO_QUALITY_PROFILE
    global CLOUDFLARE_TOKEN, CUSTOM_DOMAIN, TAILSCALE_DOMAIN, DUCKDNS_DOMAIN, DUCKDNS_TOKEN
    _current_cfg = load_config()
    DATA_DIR = get_data_dir()
    LIBRARY_DIR = get_library_dir()
    TRACKS_DIR = get_tracks_dir()
    PLAYLISTS_DIR = get_playlists_dir()
    DB_PATH = get_db_path()
    SECRET_FILE = get_secret_file()
    HOST_TOKEN = get_host_token()

    HOST_PIN = os.getenv("TIKI_HOST_PIN", _current_cfg.get("host_pin", "1234"))
    PORT = int(os.getenv("TIKI_PORT", str(_current_cfg.get("port", 8000))))
    PUBLIC_HOST = os.getenv("TIKI_PUBLIC_HOST", _current_cfg.get("public_host", "")).strip()
    TARGET_LUFS = float(_current_cfg.get("target_lufs", -16.0))
    DEFAULT_INTERMISSION_SECONDS = int(_current_cfg.get("intermission_seconds", 15))
    REMOTE_MODE = os.getenv("TIKI_REMOTE_MODE", _current_cfg.get("remote_mode", "cloudflare_quick"))
    REMOTE_PORT = int(os.getenv("TIKI_REMOTE_PORT", str(_current_cfg.get("remote_port", 38480))))
    BAR_NAME = os.getenv("TIKI_BAR_NAME", _current_cfg.get("bar_name", "The Tiki Hut Karaoke Lounge"))
    VIDEO_QUALITY_PROFILE = _current_cfg.get("video_quality_profile", "pro")
    CLOUDFLARE_TOKEN = os.getenv("TIKI_CLOUDFLARE_TOKEN", _current_cfg.get("cloudflare_token", "")).strip()
    CUSTOM_DOMAIN = os.getenv("TIKI_CUSTOM_DOMAIN", _current_cfg.get("custom_domain", "")).strip()
    TAILSCALE_DOMAIN = os.getenv("TIKI_TAILSCALE_DOMAIN", _current_cfg.get("tailscale_domain", "")).strip()
    DUCKDNS_DOMAIN = os.getenv("TIKI_DUCKDNS_DOMAIN", _current_cfg.get("duckdns_domain", "")).strip()
    DUCKDNS_TOKEN = os.getenv("TIKI_DUCKDNS_TOKEN", _current_cfg.get("duckdns_token", "")).strip()

def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config()
    cfg.update(updates)
    cfg["configured"] = True
    cfg_path = get_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    ensure_storage_dirs()
    refresh_globals()
    if "video_quality_profile" in updates:
        try:
            from database import set_setting
            set_setting("video_quality_profile", str(updates["video_quality_profile"]))
        except Exception:
            pass
    return cfg

def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg.get("configured", False))

def get_path_info(path_str: str) -> Dict[str, Any]:
    """Inspects a path for valid write permissions, existence, and free disk space."""
    import shutil
    try:
        p = Path(path_str).expanduser().resolve()
        probe = p
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        usage = shutil.disk_usage(probe)
        free_gb = round(usage.free / (1024**3), 2)
        total_gb = round(usage.total / (1024**3), 2)
        used_gb = round(usage.used / (1024**3), 2)
        writable = os.access(probe, os.W_OK)
        return {
            "valid": True,
            "path": str(p),
            "exists": p.exists(),
            "writable": writable,
            "free_gb": free_gb,
            "total_gb": total_gb,
            "used_gb": used_gb
        }
    except Exception as e:
        return {
            "valid": False,
            "error": str(e)
        }

def get_storage_status() -> Dict[str, Any]:
    """Returns disk usage, percentage, status color (green/orange/red), and local library stats."""
    import shutil
    tracks_dir = get_tracks_dir()
    probe = tracks_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent

    usage = shutil.disk_usage(probe)
    total_gb = round(usage.total / (1024**3), 1)
    used_gb = round(usage.used / (1024**3), 1)
    free_gb = round(usage.free / (1024**3), 1)
    used_pct = round((usage.used / usage.total) * 100, 1)

    lib_bytes = 0
    track_count = 0
    dirs_to_scan = [tracks_dir, get_sing_along_dir()]
    for d in dirs_to_scan:
        if d.exists():
            for f in d.glob("**/*"):
                if f.is_file():
                    try:
                        lib_bytes += f.stat().st_size
                        if f.suffix.lower() == ".mp4":
                            track_count += 1
                    except Exception:
                        pass
    lib_mb = round(lib_bytes / (1024**2), 1)
    lib_gb = round(lib_bytes / (1024**3), 2)

    # Live singer performance recordings metrics
    rec_dir = get_recordings_dir()
    rec_bytes = 0
    rec_count = 0
    singer_dirs = set()
    if rec_dir.exists():
        try:
            for f in rec_dir.glob("**/*"):
                if f.is_file() and f.suffix.lower() in (".mp3", ".webm", ".wav", ".m4a"):
                    rec_bytes += f.stat().st_size
                    rec_count += 1
                    singer_dirs.add(f.parent.name)
        except Exception:
            pass
    rec_mb = round(rec_bytes / (1024**2), 1)

    # Color thresholds: green default, orange at >= 75%, red at >= 90%
    if used_pct >= 90.0:
        status_color = "red"
        status_level = "critical"
        message = f"🚨 Storage Critical: {used_pct}% Full ({free_gb} GB remaining). Pruning recommended!"
    elif used_pct >= 75.0:
        status_color = "orange"
        status_level = "warning"
        message = f"⚠️ Storage Warning: {used_pct}% Full ({free_gb} GB remaining)."
    else:
        status_color = "green"
        status_level = "normal"
        message = f"🌴 Storage Healthy: {used_pct}% Full ({free_gb} GB remaining)."

    return {
        "partition_path": str(probe),
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "used_percent": used_pct,
        "status_color": status_color,
        "status_level": status_level,
        "message": message,
        "library_bytes": lib_bytes,
        "library_mb": lib_mb,
        "library_gb": lib_gb,
        "downloaded_track_count": track_count,
        "recordings_dir": str(rec_dir),
        "recordings_bytes": rec_bytes,
        "recordings_mb": rec_mb,
        "recordings_count": rec_count,
        "recordings_singers_count": len(singer_dirs)
    }

# Initial export of globals
DATA_DIR = get_data_dir()
LIBRARY_DIR = get_library_dir()
TRACKS_DIR = get_tracks_dir()
PLAYLISTS_DIR = get_playlists_dir()
SING_ALONG_DIR = get_sing_along_dir()
RECORDINGS_DIR = get_recordings_dir()
DB_PATH = get_db_path()
SECRET_FILE = get_secret_file()
HOST_TOKEN = get_host_token()

_current_cfg = load_config()
HOST_PIN = os.getenv("TIKI_HOST_PIN", _current_cfg.get("host_pin", "1234"))
PORT = int(os.getenv("TIKI_PORT", str(_current_cfg.get("port", 8000))))
PUBLIC_HOST = os.getenv("TIKI_PUBLIC_HOST", _current_cfg.get("public_host", "")).strip()
TARGET_LUFS = float(_current_cfg.get("target_lufs", -16.0))
DEFAULT_INTERMISSION_SECONDS = int(_current_cfg.get("intermission_seconds", 15))
REMOTE_MODE = os.getenv("TIKI_REMOTE_MODE", _current_cfg.get("remote_mode", "cloudflare_quick"))
REMOTE_PORT = int(os.getenv("TIKI_REMOTE_PORT", str(_current_cfg.get("remote_port", 38480))))
BAR_NAME = os.getenv("TIKI_BAR_NAME", _current_cfg.get("bar_name", "The Tiki Hut Karaoke Lounge"))
VIDEO_QUALITY_PROFILE = _current_cfg.get("video_quality_profile", "pro")
