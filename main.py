#!/usr/bin/env python3
import sys
import os
import time
import signal
import atexit
import argparse
import webbrowser
import threading
import subprocess
import re
import tempfile
import json
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import shutil

# Ensure project root is in python path
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).resolve().parent
    # Clean internal AppImage / PyInstaller environment so child processes (firefox, chrome, python3, xdg-open, /bin/sh)
    # use host system libraries and standard library instead of container bundle
    os.environ.pop("PYTHONHOME", None)
    os.environ.pop("PYTHONPATH", None)
    if "LD_LIBRARY_PATH_ORIG" in os.environ:
        os.environ["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH_ORIG"]
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)
else:
    BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Explicit Windows Application User Model ID (AUMID) for taskbar grouping & persistent tray settings
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Todd.TikiKaraoke.Lounge.1.0")
    except Exception:
        pass

from config import (
    DATA_DIR, HOST_TOKEN, HOST_PIN, PORT, TARGET_LUFS,
    load_config, is_configured, get_bundled_binary_path,
    find_system_browser, get_available_displays,
    open_url_in_preferred_browser, get_available_browsers
)
from app import LOCAL_IP, PUBLIC_IP, get_base_url, get_public_host, register_shutdown_trigger, register_focus_hub_trigger
from tray import start_tray, stop_tray

LOG_FILE = DATA_DIR / "tiki_karaoke.log"

class SafeLogger:
    def __init__(self, filepath: Optional[Path], original_stream=None):
        self.filepath = filepath
        self.original_stream = original_stream
        self.file = None
        self.encoding = getattr(original_stream, "encoding", "utf-8") or "utf-8"
        self.errors = getattr(original_stream, "errors", "replace") or "replace"
        self.closed = False
        if self.filepath:
            try:
                self.filepath.parent.mkdir(parents=True, exist_ok=True)
                self.file = open(self.filepath, "a", encoding="utf-8", buffering=1)
            except Exception:
                self.file = None

    def write(self, data):
        if self.original_stream:
            try:
                self.original_stream.write(data)
                self.original_stream.flush()
            except Exception:
                pass
        if self.file:
            try:
                self.file.write(data)
                self.file.flush()
            except Exception:
                pass

    def flush(self):
        if self.original_stream:
            try:
                self.original_stream.flush()
            except Exception:
                pass
        if self.file:
            try:
                self.file.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        if self.original_stream and hasattr(self.original_stream, "isatty"):
            try:
                return bool(self.original_stream.isatty())
            except Exception:
                pass
        return False

    def fileno(self):
        if self.original_stream and hasattr(self.original_stream, "fileno"):
            try:
                return self.original_stream.fileno()
            except Exception:
                pass
        if self.file:
            try:
                return self.file.fileno()
            except Exception:
                pass
        raise OSError("SafeLogger stream has no fileno")

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

# Tee output to log file and protect windowed Python execution
sys.stdout = SafeLogger(LOG_FILE, sys.stdout)
sys.stderr = SafeLogger(LOG_FILE, sys.stderr)

TIKI_BANNER = r"""
  _______ _ _    _    _  __                     _        
 |__   __(_) |  (_)  | |/ /                    | |       
    | |   _| | ___   | ' / __ _ _ __ __ _  ___ | | _____ 
    | |  | | |/ / |  |  < / _` | '__/ _` |/ _ \| |/ / _ \
    | |  | |   <| |  | . \ (_| | | | (_| | (_) |   <  __/
    |_|  |_|_|\_\_|  |_|\_\__,_|_|  \__,_|\___/|_|\_\___|
      🌴 🌺  KARAOKE LOUNGE & NEON BAR  🍹 🌴
"""

PID_FILE = DATA_DIR / "karaoke.pid"
active_tunnel_proc = None
launched_browser_procs: List[subprocess.Popen] = []
is_shutting_down = False
global_server = None
active_hub = None
_single_instance_lock = None

def get_console_subprocess_kwargs() -> dict:
    """Returns subprocess kwargs for console utilities (suppresses console windows on Windows)."""
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        return {"creationflags": creationflags}
    return {"preexec_fn": os.setsid}

def get_browser_subprocess_kwargs() -> dict:
    """Returns subprocess kwargs for GUI browser windows (strictly without CREATE_NO_WINDOW)."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"preexec_fn": os.setsid}

# Alias for backward compatibility
get_process_group_kwargs = get_console_subprocess_kwargs

def check_single_instance() -> bool:
    """Ensures only one instance of Tiki Karaoke runs at a time."""
    global _single_instance_lock
    if os.environ.get("TIKI_TESTING"):
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            ERROR_ALREADY_EXISTS = 183
            mutex_name = "Local\\TikiKaraokeSingleInstanceMutex"
            mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
            last_err = ctypes.windll.kernel32.GetLastError()
            if last_err == ERROR_ALREADY_EXISTS:
                return False
            _single_instance_lock = mutex
            return True
        except Exception:
            return True
    else:
        try:
            import fcntl
            lock_path = DATA_DIR / ".tiki_karaoke.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            f = open(lock_path, "w")
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _single_instance_lock = f
            return True
        except (BlockingIOError, OSError):
            return False
        except Exception:
            return True

def set_windows_sleep_prevention(enable: bool = True):
    """Prevents Windows system and display from entering sleep mode while Tiki Karaoke is active."""
    if sys.platform == "win32":
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            if enable:
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                )
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass

def init_windows_dpi_awareness():
    """Configures high-DPI awareness on Windows to prevent blurry GUI on 4K TVs and high-res screens."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

_windows_job_handle = None
_windows_ctrl_handler_ref = None

def setup_windows_job_object():
    """Binds the current process and all child processes to a Windows Job Object configured to KILL_ON_JOB_CLOSE."""
    global _windows_job_handle
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            return

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        JobObjectExtendedLimitInformation = 9
        res = kernel32.SetInformationJobObject(
            h_job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info)
        )
        if not res:
            return

        h_process = kernel32.GetCurrentProcess()
        if kernel32.AssignProcessToJobObject(h_job, h_process):
            _windows_job_handle = h_job
    except Exception:
        pass

def setup_windows_ctrl_handler():
    """Catches Windows console close / logoff / shutdown events and executes graceful service cleanup."""
    global _windows_ctrl_handler_ref
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _ctrl_handler(ctrl_type):
            cleanup_all_services()
            return False

        _windows_ctrl_handler_ref = HandlerRoutine(_ctrl_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_windows_ctrl_handler_ref, True)
    except Exception:
        pass

def kill_process_tree(proc: Optional[subprocess.Popen], timeout: float = 2.0):
    """Gracefully terminates proc and its child processes across Unix and Windows."""
    if not proc or proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=timeout)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass

def print_terminal_qr(url: str, label: str):
    print(f"\n--- {label} ---")
    print(url)
    try:
        import qrcode
        qr = qrcode.QRCode()
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:
        pass

def cleanup_all_services():
    """Ensures all spawned background processes, browser windows, and tunnels are cleanly terminated."""
    global active_tunnel_proc, launched_browser_procs, is_shutting_down
    if is_shutting_down:
        return
    is_shutting_down = True

    set_windows_sleep_prevention(False)
    print("\n🌴 [Shutdown] Gracefully shutting down Tiki Karaoke Lounge...")
    stop_tray()

    # Terminate dedicated browser windows spawned for Tiki Karaoke
    for proc in list(launched_browser_procs):
        try:
            if proc and proc.poll() is None:
                kill_process_tree(proc)
        except Exception as e:
            print(f"[Shutdown] Notice closing browser proc {getattr(proc, 'pid', 'unknown')}: {e}")
    launched_browser_procs.clear()

    # On Windows: Terminate any leftover dedicated browser profile processes (tiki_stage_*, tiki_host_*)
    # Strictly DO NOT broadcast WM_CLOSE to window titles to avoid closing Control Hub or other apps
    if sys.platform == "win32":
        try:
            ps_cmd = (
                'Get-CimInstance Win32_Process | Where-Object { '
                '    ($_.CommandLine -like "*tiki_stage_*" -or $_.CommandLine -like "*tiki_host_*") '
                '    -and $_.ProcessId -ne $PID '
                '} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4.0,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        except Exception:
            pass

    if active_tunnel_proc and active_tunnel_proc.poll() is None:
        print("[Shutdown] Stopping remote tunnel process...")
        kill_process_tree(active_tunnel_proc)
        active_tunnel_proc = None

    # Forcefully terminate any leftover or orphaned background helper processes
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            subprocess.run(["taskkill", "/F", "/IM", "bore.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            subprocess.run(["taskkill", "/F", "/IM", "tiki-mic-router.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
    else:
        try:
            subprocess.run(["pkill", "-9", "-f", "cloudflared tunnel"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False)
            subprocess.run(["pkill", "-9", "-f", "bore local"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False)
        except Exception:
            pass

    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except Exception:
            pass

    try:
        from audio_router import audio_router
        audio_router.stop_loopback()
    except Exception:
        pass

    print("✅ [Shutdown] All services stopped cleanly. Aloha! 🌺\n")

def handle_signal(sig, frame):
    cleanup_all_services()
    time.sleep(0.2)
    os._exit(0)

# Register clean shutdown hooks
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
atexit.register(cleanup_all_services)

def configure_firefox_profile(
    profile_path: Path,
    screen_x: int = 0,
    screen_y: int = 0,
    width: int = 1920,
    height: int = 1080,
    is_fullscreen: bool = False
):
    profile_path.mkdir(parents=True, exist_ok=True)
    user_js = profile_path / "user.js"
    prefs = [
        'user_pref("media.autoplay.default", 0);',
        'user_pref("media.autoplay.blocking_policy", 0);',
        'user_pref("media.autoplay.enabled.user-gestures-needed", false);',
        'user_pref("media.autoplay.ask-permission", false);',
        'user_pref("dom.webnotifications.enabled", false);',
        'user_pref("browser.sessionstore.resume_from_crash", false);',
        'user_pref("browser.shell.checkDefaultBrowser", false);',
        'user_pref("toolkit.telemetry.enabled", false);',
        'user_pref("dom.allow_scripts_to_close_windows", true);',
        'user_pref("browser.tabs.closeWindowWithLastTab", true);',
        'user_pref("dom.disable_window_move_resize", false);',
        'user_pref("dom.disable_window_flip", false);',
    ]
    try:
        user_js.write_text("\n".join(prefs) + "\n")
    except Exception as e:
        print(f"[Launcher] Warning: Could not write Firefox user.js prefs: {e}")

    # Explicitly configure xulstore.json to guarantee window position, size, and sizemode (normal vs fullscreen)
    xulstore = profile_path / "xulstore.json"
    sizemode = "fullscreen" if is_fullscreen else "normal"
    store_data = {
        "chrome://browser/content/browser.xhtml": {
            "main-window": {
                "screenX": str(screen_x),
                "screenY": str(screen_y),
                "width": str(width),
                "height": str(height),
                "sizemode": sizemode
            }
        }
    }
    try:
        xulstore.write_text(json.dumps(store_data, indent=2))
    except Exception as e:
        print(f"[Launcher] Warning: Could not write Firefox xulstore.json: {e}")

def launch_display_window(
    target_url: str,
    window_name: str,
    disp_idx: int,
    disp_x: int,
    disp_y: int,
    disp_w: int,
    disp_h: int,
    is_kiosk: bool,
    pref_browser: str = "auto",
    custom_env: Optional[Dict[str, str]] = None
) -> bool:
    """
    Launches a dedicated window for Stage or Host displays in the user's preferred browser,
    prioritizing zero-click autoplay with Firefox or Chromium app-mode.
    """
    temp_root = Path(tempfile.gettempdir())
    pref = pref_browser.lower().strip()
    
    if os.environ.get("TIKI_TESTING") or os.environ.get("TIKI_DATA_DIR"):
        return True
    
    # If explicitly set to default OS browser, open directly via webbrowser
    if pref in ("default", "os", "webbrowser"):
        try:
            webbrowser.open(target_url)
            print(f"[Launcher] Default browser launched for {window_name}.")
            return True
        except Exception as e:
            print(f"[Launcher] Error opening default browser: {e}")
            return False

    env = custom_env if custom_env is not None else os.environ.copy()

    def try_firefox() -> bool:
        ff_bin = find_system_browser("firefox")
        if not ff_bin:
            return False
        try:
            profile_dir = temp_root / f"tiki_{window_name.lower().replace(' ', '_')}_firefox_profile_{disp_idx}"
            configure_firefox_profile(
                profile_dir,
                screen_x=disp_x,
                screen_y=disp_y,
                width=disp_w,
                height=disp_h,
                is_fullscreen=is_kiosk
            )
            ff_env = env.copy()
            if not is_kiosk and sys.platform != "win32":
                ff_env["GDK_BACKEND"] = "x11"
                ff_env["MOZ_ENABLE_WAYLAND"] = "0"
            cmd = [
                ff_bin,
                "--no-remote",
                "--profile", str(profile_dir),
                "--new-instance"
            ]
            if is_kiosk:
                cmd.extend(["--kiosk", f"--kiosk-monitor={disp_idx}"])
            else:
                cmd.append("--new-window")
            cmd.append(target_url)
            proc = subprocess.Popen(cmd, env=ff_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **get_browser_subprocess_kwargs())
            launched_browser_procs.append(proc)
            print(f"[Launcher] ✅ Dedicated {window_name} launched via Firefox on monitor {disp_idx}!")
            return True
        except Exception as e:
            print(f"[Launcher] Firefox launch error for {window_name}: {e}")
            return False

    def try_chromium(candidate_name: str) -> bool:
        c_bin = find_system_browser(candidate_name)
        if not c_bin:
            return False
        try:
            chrome_data = temp_root / f"tiki_{window_name.lower().replace(' ', '_')}_chrome_{disp_idx}"
            cmd = [
                c_bin,
                f"--app={target_url}",
                f"--user-data-dir={chrome_data}",
                "--no-first-run",
                "--no-default-browser-check",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-features=PreloadMediaEngagementData",
                f"--window-position={disp_x},{disp_y}",
                f"--window-size={disp_w},{disp_h}"
            ]
            if is_kiosk:
                cmd.append("--start-fullscreen")
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **get_browser_subprocess_kwargs())
            launched_browser_procs.append(proc)
            print(f"[Launcher] ✅ Dedicated {window_name} launched via {candidate_name} on monitor {disp_idx}!")
            return True
        except Exception as e:
            print(f"[Launcher] {candidate_name} launch error for {window_name}: {e}")
            return False

    # 1. User preferred specific browser
    if pref == "firefox":
        if try_firefox():
            return True
    elif pref in ("chrome", "google-chrome"):
        for c in ["google-chrome", "google-chrome-stable", "chrome"]:
            if try_chromium(c):
                return True
    elif pref in ("edge", "microsoft-edge", "msedge"):
        for c in ["microsoft-edge", "microsoft-edge-stable", "msedge"]:
            if try_chromium(c):
                return True
    elif pref in ("brave", "brave-browser"):
        for c in ["brave-browser", "brave"]:
            if try_chromium(c):
                return True
    elif pref == "chromium":
        for c in ["chromium", "chromium-browser"]:
            if try_chromium(c):
                return True

    # 2. Auto mode or preferred browser wasn't found: Top pick is Firefox (zero-gesture autoplay)
    if try_firefox():
        return True

    # 3. Fallback to Chromium browsers
    for c in ["microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser"]:
        if try_chromium(c):
            return True

    # 4. Final fallback: OS default browser
    try:
        webbrowser.open(target_url)
        print(f"[Launcher] Default browser launched for {window_name}.")
        return True
    except Exception as e:
        print(f"[Launcher] Could not open browser automatically: {e}")
        return False


def open_browser_delayed(stage_url: str, host_url: str = "", delay: float = 1.5):
    time.sleep(delay)
    if not is_configured():
        base_origin = stage_url.split("/stage")[0] if "/stage" in stage_url else "http://127.0.0.1:8000"
        setup_url = f"{base_origin}/setup"
        print(f"[Launcher] Fresh installation detected. Opening single Setup Wizard window: {setup_url}")
        open_url_in_preferred_browser(setup_url)
        return

    cfg = load_config()
    pref_browser = str(cfg.get("preferred_browser", "auto"))

    # 1. Main Stage Display Window
    stage_disp_idx = int(cfg.get("stage_display", 0))
    stage_disp_name = cfg.get("stage_display_name", "Primary Display")
    stage_disp_x = int(cfg.get("stage_display_x", 0))
    stage_disp_y = int(cfg.get("stage_display_y", 0))
    stage_disp_w = int(cfg.get("stage_display_width", 1920))
    stage_disp_h = int(cfg.get("stage_display_height", 1080))
    stage_kiosk = bool(cfg.get("kiosk_mode", False))

    try:
        available_monitors = get_available_displays()
        matched_stage = next((d for d in available_monitors if d.get("id") == stage_disp_idx), None)
        if matched_stage:
            stage_disp_x = matched_stage.get("x", stage_disp_x)
            stage_disp_y = matched_stage.get("y", stage_disp_y)
            stage_disp_w = matched_stage.get("width", stage_disp_w)
            stage_disp_h = matched_stage.get("height", stage_disp_h)
            stage_disp_name = matched_stage.get("name", stage_disp_name)
    except Exception as e:
        print(f"[Launcher] Notice: could not enrich stage display info: {e}")

    stage_sep = "&" if "?" in stage_url else "?"
    stage_target_url = f"{stage_url}{stage_sep}disp_x={stage_disp_x}&disp_y={stage_disp_y}&disp_w={stage_disp_w}&disp_h={stage_disp_h}&kiosk={'true' if stage_kiosk else 'false'}"

    mode_label = "Fullscreen Kiosk" if stage_kiosk else "Windowed"
    print(f"[Launcher] Opening Stage Display on Monitor {stage_disp_idx} ({stage_disp_name}, {mode_label}, {stage_disp_x},{stage_disp_y}) [Browser: {pref_browser}]: {stage_target_url}")

    audio_device = cfg.get("audio_output_device", "default")
    audio_input = cfg.get("audio_input_device", "default")
    stage_env = os.environ.copy()
    if audio_device and audio_device != "default":
        stage_env["PULSE_SINK"] = audio_device
        print(f"[Launcher] 🔊 Routing Stage Audio directly to sink: {audio_device}")
    if audio_input and audio_input != "default":
        stage_env["PULSE_SOURCE"] = audio_input
        print(f"[Launcher] 🎤 Routing Stage Microphone directly to source: {audio_input}")

    launch_display_window(
        target_url=stage_target_url,
        window_name="Stage Display",
        disp_idx=stage_disp_idx,
        disp_x=stage_disp_x,
        disp_y=stage_disp_y,
        disp_w=stage_disp_w,
        disp_h=stage_disp_h,
        is_kiosk=stage_kiosk,
        pref_browser=pref_browser,
        custom_env=stage_env
    )

    # 2. Host DJ Control Window (for multi-display setups)
    host_disp_enabled = bool(cfg.get("host_display_enabled", False))
    if host_disp_enabled and host_url:
        time.sleep(1.0)
        host_disp_idx = int(cfg.get("host_display", 1))
        host_disp_name = cfg.get("host_display_name", f"Monitor {host_disp_idx}")
        host_disp_x = int(cfg.get("host_display_x", 0))
        host_disp_y = int(cfg.get("host_display_y", 0))
        host_disp_w = int(cfg.get("host_display_width", 1920))
        host_disp_h = int(cfg.get("host_display_height", 1080))
        host_kiosk = bool(cfg.get("host_kiosk_mode", False))

        try:
            available_monitors = get_available_displays()
            matched_host = next((d for d in available_monitors if d.get("id") == host_disp_idx), None)
            if matched_host:
                host_disp_x = matched_host.get("x", host_disp_x)
                host_disp_y = matched_host.get("y", host_disp_y)
                host_disp_w = matched_host.get("width", host_disp_w)
                host_disp_h = matched_host.get("height", host_disp_h)
                host_disp_name = matched_host.get("name", host_disp_name)
        except Exception as e:
            print(f"[Launcher] Notice: could not enrich host display info: {e}")

        host_sep = "&" if "?" in host_url else "?"
        host_target_url = f"{host_url}{host_sep}disp_x={host_disp_x}&disp_y={host_disp_y}&disp_w={host_disp_w}&disp_h={host_disp_h}&kiosk={'true' if host_kiosk else 'false'}"

        host_mode_label = "Fullscreen Kiosk" if host_kiosk else "App Window"
        print(f"[Launcher] Opening Host DJ Control on Monitor {host_disp_idx} ({host_disp_name}, {host_mode_label}, {host_disp_x},{host_disp_y}) [Browser: {pref_browser}]: {host_target_url}")

        launch_display_window(
            target_url=host_target_url,
            window_name="Host DJ Control",
            disp_idx=host_disp_idx,
            disp_x=host_disp_x,
            disp_y=host_disp_y,
            disp_w=host_disp_w,
            disp_h=host_disp_h,
            is_kiosk=host_kiosk,
            pref_browser=pref_browser
        )

def start_cloudflare_quick_tunnel(port: int):
    bin_path = get_bundled_binary_path("cloudflared")
    if not bin_path.exists():
        print("[Tunnel] ⚠️ cloudflared binary not found. Remote tunnel unavailable.")
        return None, None

    # Terminate any stale cloudflared processes from prior runs or crashes
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.run(["pkill", "-9", "-f", "cloudflared tunnel"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False)
    except Exception:
        pass

    print("[Tunnel] 🔒 Establishing secure Cloudflare Quick Tunnel (Trusted HTTPS)...")
    try:
        proc = subprocess.Popen(
            [str(bin_path), "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            **get_process_group_kwargs()
        )
    except Exception as e:
        print(f"[Tunnel Error] Failed to launch cloudflared: {e}")
        return None, None

    url = None
    start = time.time()
    while time.time() - start < 12:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if m:
            url = m.group(0)
            break

    # Background reader to prevent pipe lock
    def _drain(pipe):
        for _ in iter(pipe.readline, ''):
            pass
    threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()

    return url, proc

def start_cloudflare_named_tunnel(token: str, custom_domain: str):
    bin_path = get_bundled_binary_path("cloudflared")
    if not bin_path.exists():
        print("[Tunnel] ⚠️ cloudflared binary not found")
        return None, None

    if not token:
        print("[Tunnel] ⚠️ Cloudflare Named Tunnel selected but no tunnel token configured.")
        return None, None

    # Terminate any stale cloudflared processes from prior runs or crashes
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.run(["pkill", "-9", "-f", "cloudflared tunnel"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, check=False)
    except Exception:
        pass

    print(f"[Tunnel] 🔒 Launching Cloudflare Named Tunnel ({custom_domain or 'Branded Domain'})...")
    try:
        proc = subprocess.Popen(
            [str(bin_path), "tunnel", "run", "--token", token],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            **get_process_group_kwargs()
        )
    except Exception as e:
        print(f"[Tunnel Error] Failed to launch named tunnel: {e}")
        return None, None

    def _drain(pipe):
        for line in iter(pipe.readline, ''):
            if "error" in line.lower():
                print(f"[Cloudflare Tunnel] {line.strip()}")
    threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()

    url = f"https://{custom_domain}" if custom_domain and not custom_domain.startswith("http") else custom_domain
    return url, proc

def start_tailscale_funnel(port: int, tailnet_domain: str = ""):
    ts_bin = shutil.which("tailscale")
    if not ts_bin:
        print("[Tunnel] ⚠️ 'tailscale' CLI is not installed on this system.")
        print("         To enable Tailscale Funnel: curl -fsSL https://tailscale.com/install.sh | sh")
        return None, None

    print(f"[Tunnel] 🔒 Enabling Tailscale Funnel on port {port}...")
    try:
        proc = subprocess.Popen(
            [ts_bin, "funnel", "--bg", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        proc.wait(timeout=5)
    except Exception as e:
        print(f"[Tailscale Error] {e}")

    url = None
    if tailnet_domain:
        url = f"https://{tailnet_domain}" if not tailnet_domain.startswith("http") else tailnet_domain
    else:
        try:
            res = subprocess.run([ts_bin, "status", "--json"], capture_output=True, text=True, timeout=3)
            import json as _json
            data = _json.loads(res.stdout)
            dns_name = data.get("Self", {}).get("DNSName", "").rstrip(".")
            if dns_name:
                url = f"https://{dns_name}"
        except Exception:
            pass

    return url, None

def start_permanent_tunnel(port: int, remote_port: int = 38480):
    global active_tunnel_proc
    bin_path = get_bundled_binary_path("bore")
    if not bin_path.exists():
        print("[Remote] bore binary not found")
        return None, None

    # Kill any stale local bore processes to free up remote static port
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "bore.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.run(["pkill", "-9", "-f", "bore local"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.4)
    except Exception:
        pass

    print(f"[Remote] Establishing permanent static tunnel on bore.pub:{remote_port}...")
    proc = subprocess.Popen(
        [str(bin_path), "local", str(port), "--to", "bore.pub", "--port", str(remote_port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **get_process_group_kwargs()
    )
    active_tunnel_proc = proc

    # Read output in background to monitor tunnel health
    def _drain(pipe):
        for line in iter(pipe.readline, ''):
            if "listening at" in line:
                print(f"[Remote] ✅ {line.strip()}")
            elif "error" in line.lower():
                print(f"[Remote Error] {line.strip()}")
    threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()

    time.sleep(1.2)
    if proc.poll() is not None:
        print("[Remote Warning] bore process exited early. Retrying once...")
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/IM", "bore.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                subprocess.run(["pkill", "-9", "-f", "bore local"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            proc = subprocess.Popen(
                [str(bin_path), "local", str(port), "--to", "bore.pub", "--port", str(remote_port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                **get_process_group_kwargs()
            )
            active_tunnel_proc = proc
            threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()
            time.sleep(1.2)
        except Exception as e:
            print(f"[Remote Error] Retry failed: {e}")

    url = f"http://bore.pub:{remote_port}"
    os.environ["TIKI_PUBLIC_HOST"] = f"bore.pub:{remote_port}"
    return url, proc

tunnel_watchdog_thread = None

def start_tunnel_watchdog(mode: str, local_port: int, remote_port: int = 38480, token: str = "", custom_domain: str = ""):
    global tunnel_watchdog_thread
    if mode in ("local", "custom"):
        return

    def _watchdog_loop():
        global active_tunnel_proc, is_shutting_down
        # Wait for initial server and tunnel launch
        time.sleep(12)
        probe_counter = 0
        cf_probe_counter = 0
        cf_fail_counter = 0

        while not is_shutting_down:
            time.sleep(5)
            if is_shutting_down:
                break

            needs_restart = False
            # 1. Process termination check
            if active_tunnel_proc is not None:
                if active_tunnel_proc.poll() is not None:
                    print(f"\n[Tunnel Watchdog] ⚠️ Remote tunnel process died (exit code {active_tunnel_proc.poll()}). Auto-restarting...")
                    needs_restart = True

            # 2. Keepalive & zombie-socket probe (specifically for Bore)
            probe_counter += 5
            if not needs_restart and mode == "bore" and probe_counter >= 30:
                probe_counter = 0
                import socket
                try:
                    # Probe bore.pub on the allocated remote port with 3s timeout
                    with socket.create_connection(("bore.pub", remote_port), timeout=3.0) as sock:
                        sock.sendall(b"HEAD /api/state HTTP/1.0\r\nHost: bore.pub\r\n\r\n")
                except (ConnectionRefusedError, socket.timeout, OSError) as e:
                    print(f"\n[Tunnel Watchdog] ⚠️ bore.pub:{remote_port} connection refused or timed out ({e}). Auto-healing tunnel...")
                    needs_restart = True

            # 3. Active Cloudflare Quick Tunnel Keepalive (prevents idle edge drop)
            cf_probe_counter += 5
            if not needs_restart and mode in ("cloudflare", "cloudflare_quick") and cf_probe_counter >= 20:
                cf_probe_counter = 0
                pub_host = os.environ.get("TIKI_PUBLIC_HOST", "").strip()
                if pub_host and "trycloudflare.com" in pub_host:
                    try:
                        import urllib.request
                        req = urllib.request.Request(
                            f"https://{pub_host}/api/state",
                            headers={"User-Agent": "TikiTunnelKeepalive/1.0"}
                        )
                        with urllib.request.urlopen(req, timeout=4.0) as resp:
                            if resp.status == 200:
                                cf_fail_counter = 0
                            else:
                                cf_fail_counter += 1
                    except Exception as e:
                        cf_fail_counter += 1
                        if cf_fail_counter >= 2:
                            print(f"\n[Tunnel Watchdog] ⚠️ Cloudflare keepalive probe failed ({cf_fail_counter}/3): {e}")

                    if cf_fail_counter >= 3:
                        print(f"\n[Tunnel Watchdog] ⚠️ Cloudflare tunnel unresponsive after 3 probes. Auto-healing tunnel...")
                        needs_restart = True
                        cf_fail_counter = 0

            if needs_restart and not is_shutting_down:
                if active_tunnel_proc:
                    kill_process_tree(active_tunnel_proc)
                    active_tunnel_proc = None

                time.sleep(1.0)
                new_url = None
                if mode == "bore":
                    new_url, active_tunnel_proc = start_permanent_tunnel(local_port, remote_port)
                elif mode in ("cloudflare", "cloudflare_quick"):
                    new_url, active_tunnel_proc = start_cloudflare_quick_tunnel(local_port)
                elif mode == "cloudflare_named":
                    new_url, active_tunnel_proc = start_cloudflare_named_tunnel(token, custom_domain)

                if new_url:
                    clean_host = new_url.replace("https://", "").replace("http://", "").rstrip("/")
                    os.environ["TIKI_PUBLIC_HOST"] = clean_host
                    if new_url.startswith("https://"):
                        os.environ["TIKI_USE_HTTPS"] = "1"
                    from config import refresh_globals
                    refresh_globals()
                    if active_hub:
                        host_param = f"?token={HOST_TOKEN}" if HOST_TOKEN else ""
                        active_hub.update_network_urls({
                            "public": new_url,
                            "join": f"{new_url}/join",
                            "host_remote": f"{new_url}/host{host_param}"
                        })
                    try:
                        import app as app_module
                        if hasattr(app_module, "broadcast_network_update_sync"):
                            app_module.broadcast_network_update_sync()
                    except Exception as e:
                        print(f"[Tunnel Watchdog] Could not broadcast network update: {e}")

    tunnel_watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True)
    tunnel_watchdog_thread.start()

def check_port_available(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(0.5)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False

def main():
    global active_tunnel_proc
    cfg = load_config()

    parser = argparse.ArgumentParser(description="Tiki Karaoke Lounge Full Application Orchestrator")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(cfg.get("port", PORT)), help=f"Port to run on (default: {cfg.get('port', PORT)})")
    parser.add_argument("--remote-port", type=int, default=int(cfg.get("remote_port", 38480)), help="Permanent public port on bore.pub (default: 38480)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch browser on start")
    parser.add_argument("--no-gui", action="store_true", help="Disable native desktop Control Hub window")
    parser.add_argument("--headless", action="store_true", help="Run strictly headless server (no GUI, no browser)")
    parser.add_argument("--no-tray", action="store_true", help="Disable system tray icon")
    parser.add_argument("--local-only", action="store_true", help="Run on local network only (disable remote tunnels)")
    parser.add_argument("--tunnel", action="store_true", help="Force ephemeral Cloudflare HTTPS tunnel (Trusted SSL)")
    parser.add_argument("--bore", action="store_true", help="Force legacy Bore static port tunnel (HTTP)")
    parser.add_argument("--debug-tray", action="store_true", help="Diagnose system tray icon and menu setup")
    args = parser.parse_args()

    if args.debug_tray:
        from tray import debug_tray_status
        debug_tray_status()
        sys.exit(0)

    # Initialize high-DPI scaling, Windows Job Object (kills child processes on exit), and Ctrl handler
    init_windows_dpi_awareness()
    setup_windows_job_object()
    setup_windows_ctrl_handler()

    # Enforce single instance across the system
    if not check_single_instance():
        print("⚠️  [Tiki Karaoke] Another instance of Tiki Karaoke is already running.")
        if sys.platform == "win32":
            try:
                ps_focus = (
                    '$code = @\'\n'
                    'using System;\n'
                    'using System.Runtime.InteropServices;\n'
                    'public class Win32 {\n'
                    '    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);\n'
                    '    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);\n'
                    '}\n'
                    '\'@\n'
                    'Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue\n'
                    'Get-Process | Where-Object { $_.MainWindowTitle -like "*Control Hub*" } | ForEach-Object {\n'
                    '    [Win32]::ShowWindow($_.MainWindowHandle, 9)\n'
                    '    [Win32]::SetForegroundWindow($_.MainWindowHandle)\n'
                    '}'
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_focus],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2.0,
                    check=False,
                    **get_console_subprocess_kwargs()
                )
            except Exception:
                pass
        sys.exit(0)

    # Prevent Windows computer and display from sleeping during party/karaoke show
    set_windows_sleep_prevention(True)

    # Write PID file for process tracking
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        print(f"[Warning] Could not write PID file: {e}")

    # 1. Pre-flight dependency check (FFmpeg / FFprobe)
    ffmpeg_ok = bool(shutil.which("ffmpeg") or get_bundled_binary_path("ffmpeg").exists())
    if not ffmpeg_ok:
        print("\n" + "!" * 68)
        print("⚠️  [NOTICE] FFmpeg is not installed on this system.")
        print("   YouTube song downloading and audio extraction will be unavailable.")
        print("   To enable full functionality, install FFmpeg:")
        if sys.platform == "win32":
            print("     • Windows (winget):    winget install Gyan.FFmpeg")
            print("     • Windows (choco):     choco install ffmpeg")
        else:
            print("     • Ubuntu/Debian/Mint:  sudo apt install -y ffmpeg")
            print("     • Arch/Manjaro:        sudo pacman -S ffmpeg")
            print("     • Fedora/RHEL:         sudo dnf install ffmpeg")
        print("!" * 68 + "\n")

    # 2. Port availability & dynamic auto-fallback
    target_port = args.port
    if not check_port_available(args.host, target_port):
        print(f"\n⚠️  [Port Conflict] Port {target_port} is already in use by another process.")
        found_port = None
        for candidate in range(target_port + 1, target_port + 20):
            if check_port_available(args.host, candidate):
                found_port = candidate
                break
        if found_port:
            print(f"👉 Automatically switching to available port: {found_port}\n")
            target_port = found_port
        else:
            print(f"\n❌ [Error] Could not find an available port starting from {args.port} to {args.port + 20}!")
            print("Run './start.sh --stop' to terminate existing instances, or specify a different port with '--port'.\n")
            sys.exit(1)

    os.environ["TIKI_PORT"] = str(target_port)
    os.environ["PORT"] = str(target_port)
    from config import refresh_globals
    refresh_globals()

    remote_mode = cfg.get("remote_mode", "cloudflare_quick")
    if args.local_only:
        remote_mode = "local"
    elif args.tunnel:
        remote_mode = "cloudflare_quick"
    elif args.bore:
        remote_mode = "bore"

    public_url = None

    if remote_mode in ("cloudflare", "cloudflare_quick"):
        public_url, active_tunnel_proc = start_cloudflare_quick_tunnel(target_port)
        if public_url:
            print(f"🔒 [Cloudflare HTTPS Active] Public URL: {public_url}")
    elif remote_mode == "cloudflare_named":
        token = cfg.get("cloudflare_token") or os.getenv("TIKI_CLOUDFLARE_TOKEN", "")
        custom_domain = cfg.get("custom_domain") or os.getenv("TIKI_CUSTOM_DOMAIN", "")
        public_url, active_tunnel_proc = start_cloudflare_named_tunnel(token, custom_domain)
        if public_url:
            print(f"🔒 [Cloudflare Branded HTTPS Active] Public URL: {public_url}")
    elif remote_mode == "tailscale":
        tailnet_domain = cfg.get("tailscale_domain") or os.getenv("TIKI_TAILSCALE_DOMAIN", "")
        public_url, active_tunnel_proc = start_tailscale_funnel(target_port, tailnet_domain)
        if public_url:
            print(f"🔒 [Tailscale Funnel Active] Public URL: {public_url}")
    elif remote_mode == "duckdns":
        domain = cfg.get("duckdns_domain") or os.getenv("TIKI_DUCKDNS_DOMAIN", "")
        if domain:
            public_url = f"https://{domain}.duckdns.org" if not domain.startswith("http") else domain
            print(f"🔒 [DuckDNS SSL Active] Public URL: {public_url}")
    elif remote_mode == "bore":
        public_url, active_tunnel_proc = start_permanent_tunnel(target_port, args.remote_port)
        if public_url:
            print(f"🌐 [Bore Static URL Active] Public URL: {public_url}")
    elif remote_mode == "custom" and cfg.get("public_host"):
        public_url = cfg["public_host"]

    if public_url:
        clean_host = public_url.replace("https://", "").replace("http://", "").rstrip("/")
        os.environ["TIKI_PUBLIC_HOST"] = clean_host
        if public_url.startswith("https://"):
            os.environ["TIKI_USE_HTTPS"] = "1"
        from config import refresh_globals
        refresh_globals()
        stage_url = f"{public_url}/stage"
        join_url = f"{public_url}/join"
        host_url = f"{public_url}/host?token={HOST_TOKEN}"
    else:
        if remote_mode != "local":
            print(f"ℹ️  [Notice] Remote tunnel ({remote_mode}) inactive or offline. Serving on Local Wi-Fi.")
        stage_url = f"{get_base_url()}/stage"
        join_url = f"{get_base_url()}/join"
        host_url = f"{get_base_url()}/host?token={HOST_TOKEN}"

    pin = cfg.get("host_pin", HOST_PIN)

    print(TIKI_BANNER)
    print("=" * 65)
    print(f"🍹 Main TV Stage Display:   {stage_url}")
    print(f"📱 Public Singer Join URL: {join_url}")
    print(f"👑 Private Host DJ URL:    {host_url}")
    print(f"🔑 Host 4-Digit PIN:       {pin}")
    print("=" * 65)

    print_terminal_qr(join_url, "SCAN WITH PHONE TO SING (PUBLIC)")
    print_terminal_qr(host_url, "SCAN FOR HOST DJ REMOTE (PRIVATE)")

    # Start auto-healing tunnel watchdog & keepalive
    cf_token = cfg.get("cloudflare_token") or os.getenv("TIKI_CLOUDFLARE_TOKEN", "")
    cf_domain = cfg.get("custom_domain") or os.getenv("TIKI_CUSTOM_DOMAIN", "")
    start_tunnel_watchdog(remote_mode, target_port, args.remote_port, cf_token, cf_domain)

    local_base = f"http://127.0.0.1:{target_port}"
    local_stage_url = f"{local_base}/stage?token={HOST_TOKEN}" if HOST_TOKEN else f"{local_base}/stage"
    local_host_url = f"{local_base}/host?token={HOST_TOKEN}" if HOST_TOKEN else f"{local_base}/"
    local_setup_url = f"{local_base}/setup"
    local_shutdown_url = f"{local_base}/api/host/shutdown?token={HOST_TOKEN}" if HOST_TOKEN else f"{local_base}/api/host/shutdown"

    if not args.no_browser:
        threading.Thread(target=open_browser_delayed, args=(local_stage_url, local_host_url), daemon=True).start()

    global global_server

    def trigger_server_shutdown():
        global global_server, is_shutting_down
        if is_shutting_down:
            return
        print("\n🛑 [Shutdown] Server shutdown initiated...")
        if global_server:
            global_server.should_exit = True
        cleanup_all_services()
        def _hard_exit():
            time.sleep(0.8)
            os._exit(0)
        threading.Thread(target=_hard_exit, daemon=True).start()

    register_shutdown_trigger(trigger_server_shutdown)

    global active_hub
    active_hub = None

    def show_active_hub():
        if active_hub:
            active_hub.show_window()

    register_focus_hub_trigger(show_active_hub)

    if not args.no_tray:
        tray_urls = {
            "stage": local_stage_url,
            "host": local_host_url,
            "join": join_url,
            "setup": local_setup_url,
            "public": public_url or join_url,
            "shutdown": local_shutdown_url,
            "focus_hub": f"{local_base}/api/host/focus_hub?token={HOST_TOKEN}" if HOST_TOKEN else f"{local_base}/api/host/focus_hub"
        }
        bar_name = cfg.get("bar_name", "The Tiki Hut Karaoke Lounge")
        start_tray(
            tray_urls,
            on_quit_callback=trigger_server_shutdown,
            on_show_hub_callback=show_active_hub,
            bar_name=bar_name
        )

    try:
        import uvicorn
        uvicorn_config = uvicorn.Config(
            "app:app",
            host=args.host,
            port=target_port,
            log_level="info",
            loop="asyncio"
        )
        server = uvicorn.Server(uvicorn_config)
        global_server = server

        enable_gui = not (args.no_gui or args.headless or os.environ.get("TIKI_HEADLESS") or os.environ.get("TIKI_TESTING") or os.environ.get("TIKI_DATA_DIR"))
        from control_hub import TikiControlHub, HAS_GUI

        if enable_gui and HAS_GUI:
            def _run_server_thread():
                try:
                    server.run()
                except Exception as e:
                    print(f"[Server Thread Exception]: {e}")

            threading.Thread(target=_run_server_thread, daemon=True).start()

            hub_urls = {
                "stage": local_stage_url,
                "host": local_host_url,
                "host_remote": host_url,
                "host_token": HOST_TOKEN,
                "join": join_url,
                "setup": local_setup_url,
                "public": public_url or join_url,
                "port": str(target_port)
            }
            hub = TikiControlHub(
                urls=hub_urls,
                on_shutdown=trigger_server_shutdown,
                bar_name=cfg.get("bar_name", "The Tiki Hut Karaoke Lounge"),
                host_pin=pin,
                log_file=LOG_FILE,
                is_tray_active=(not args.no_tray)
            )
            active_hub = hub
            hub.start()
        else:
            server.run()
    except Exception as e:
        import traceback
        print(f"[FATAL EXCEPTION IN MAIN]: {e}")
        traceback.print_exc()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        cleanup_all_services()
        time.sleep(0.2)
        os._exit(0)

if __name__ == "__main__":
    main()
