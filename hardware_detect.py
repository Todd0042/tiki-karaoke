import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tiki.hardware_detect")


def find_system_browser(name: str) -> Optional[str]:
    """Finds browser executable across Linux (native, snap, flatpak) and Windows systems."""
    name_clean = name.lower().strip()

    # 1. Direct which search
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
    """Opens a URL in the user's preferred browser (or auto-detected Firefox/Chrome/OS default)."""
    import webbrowser
    from config import load_config

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True

    cfg = load_config()
    pref = str(cfg.get("preferred_browser", "auto")).lower().strip()

    if pref in ("default", "os", "webbrowser"):
        try:
            return webbrowser.open(url)
        except Exception as e:
            logger.warning(f"[Browser] webbrowser.open failed: {e}")
            return False

    target_bin = find_system_browser(pref) if pref != "auto" else find_system_browser("firefox")

    if target_bin:
        try:
            subprocess.Popen([target_bin, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            logger.warning(f"[Browser] Could not launch {target_bin}: {e}")

    try:
        return webbrowser.open(url)
    except Exception as e:
        logger.warning(f"[Browser] Fallback webbrowser.open failed: {e}")
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
    except Exception as e:
        logger.debug(f"[Hardware] GetLogicalDrives fallback to A-Z probe: {e}")
        for letter in string.ascii_uppercase:
            p = f"{letter}:\\"
            if os.path.exists(p):
                drives.append(p)
    return drives or ["C:\\"]


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
            logger.warning(f"[Display Detection] xrandr error: {e}")

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
    except (subprocess.SubprocessError, OSError, ValueError, SyntaxError) as e:
        logger.debug(f"[Display] GTK monitor model probe unavailable: {e}")

    # 2. Windows display detection via user32.EnumDisplayMonitors
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
            except Exception as e:
                logger.debug(f"[Display] SetProcessDPIAware unavailable: {e}")

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
                is_prim = bool(mon.dwFlags & 1)
                w = mon.rcMonitor.right - mon.rcMonitor.left
                h = mon.rcMonitor.bottom - mon.rcMonitor.top
                dev_name = mon.szDevice or f"Display {idx}"
                friendly_name = ""

                try:
                    disp_dev = DISPLAY_DEVICEW()
                    disp_dev.cb = ctypes.sizeof(DISPLAY_DEVICEW)
                    if user32.EnumDisplayDevicesW(mon.szDevice, 0, ctypes.byref(disp_dev), 0):
                        mon_dev = DISPLAY_DEVICEW()
                        mon_dev.cb = ctypes.sizeof(DISPLAY_DEVICEW)
                        if user32.EnumDisplayDevicesW(disp_dev.DeviceName, 0, ctypes.byref(mon_dev), 0):
                            friendly_name = mon_dev.DeviceString.strip()
                        if not friendly_name:
                            friendly_name = disp_dev.DeviceString.strip()
                except Exception as e:
                    logger.debug(f"[Display] EnumDisplayDevicesW failed for {dev_name}: {e}")

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
            logger.warning(f"[Display Detection] Windows EnumDisplayMonitors error: {e}")

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
    """Query the bundled tiki-mic-router helper for WASAPI endpoints (Windows only)."""
    if sys.platform != "win32":
        return None
    from config import get_bundled_binary_path

    try:
        helper = get_bundled_binary_path("tiki-mic-router")
        if not helper or not Path(helper).exists():
            return None
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            [str(helper), "--list-json"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
            creationflags=creationflags
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip().splitlines()[-1])
            if isinstance(data.get("playback"), list) and isinstance(data.get("capture"), list):
                return data
    except Exception as e:
        logger.warning(f"[Audio Detection] tiki-mic-router --list-json error: {e}")
    return None


def get_available_audio_sinks() -> List[Dict[str, Any]]:
    """Returns detected system audio output devices."""
    devices = [{
        "id": "default",
        "name": "default",
        "description": "System Default Audio Output",
        "is_default": True
    }]

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
            logger.warning(f"[Audio Detection] pactl error: {e}")

    return devices


def get_available_audio_sources() -> List[Dict[str, Any]]:
    """Returns detected system audio input devices (microphones, line-in, USB receivers)."""
    devices = [{
        "id": "default",
        "name": "default",
        "description": "System Default Microphone / Input",
        "is_default": True
    }]

    if sys.platform == "win32":
        helper_data = _windows_mic_router_devices()
        if helper_data:
            for dev in helper_data["capture"]:
                name = str(dev.get("name", "")).strip()
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

    if shutil.which("pactl"):
        try:
            res = subprocess.run(["pactl", "list", "sources"], capture_output=True, text=True, timeout=2)
            source_blocks = res.stdout.split("Source #")
            for block in source_blocks[1:]:
                name_match = re.search(r"Name:\s*(\S+)", block)
                desc_match = re.search(r"Description:\s*([^\n\r]+)", block)
                if name_match:
                    src_name = name_match.group(1).strip()
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
            logger.warning(f"[Audio Input Detection] pactl error: {e}")

    return devices
