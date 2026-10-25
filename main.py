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
    find_system_browser
)
from app import LOCAL_IP, PUBLIC_IP, get_base_url, get_public_host, register_shutdown_trigger
from tray import start_tray, stop_tray

LOG_FILE = DATA_DIR / "tiki_karaoke.log"

class SafeLogger:
    def __init__(self, filepath: Optional[Path], original_stream=None):
        self.filepath = filepath
        self.original_stream = original_stream
        self.file = None
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

def get_process_group_kwargs() -> dict:
    """Returns subprocess kwargs to isolate process groups across Unix and Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"preexec_fn": os.setsid}

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
                check=False
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

    # On Windows: un-fullscreen Tiki windows (restore native title bar with [X]) and terminate dedicated profile processes
    if sys.platform == "win32":
        try:
            ps_cmd = (
                '$code = @\'\n'
                'using System;\n'
                'using System.Runtime.InteropServices;\n'
                'public class Win32 {\n'
                '    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);\n'
                '    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);\n'
                '}\n'
                '\'@\n'
                'Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue\n'
                'Get-Process | Where-Object { $_.MainWindowTitle -like "*Tiki*" -or $_.MainWindowTitle -like "*Stage*" } | ForEach-Object {\n'
                '    [Win32]::ShowWindow($_.MainWindowHandle, 9)\n'
                '    [Win32]::PostMessage($_.MainWindowHandle, 16, [IntPtr]::Zero, [IntPtr]::Zero)\n'
                '}\n'
                'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*tiki_stage_*" -or $_.CommandLine -like "*tiki_host_*" } | ForEach-Object {\n'
                '    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue\n'
                '}'
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4.0,
                check=False
            )
        except Exception:
            pass

    if active_tunnel_proc and active_tunnel_proc.poll() is None:
        print("[Shutdown] Stopping remote tunnel process...")
        kill_process_tree(active_tunnel_proc)
        active_tunnel_proc = None

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

def configure_firefox_profile(profile_path: Path):
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
    ]
    try:
        user_js.write_text("\n".join(prefs) + "\n")
    except Exception as e:
        print(f"[Launcher] Warning: Could not write Firefox user.js prefs: {e}")

def open_browser_delayed(stage_url: str, host_url: str = "", delay: float = 1.5):
    time.sleep(delay)
    if not is_configured():
        base_origin = stage_url.split("/stage")[0] if "/stage" in stage_url else "http://127.0.0.1:8000"
        setup_url = f"{base_origin}/setup"
        print(f"[Launcher] Fresh installation detected. Opening single Setup Wizard window: {setup_url}")
        try:
            webbrowser.open(setup_url)
        except Exception as e:
            print(f"[Launcher] Error opening setup browser: {e}")
        return

    cfg = load_config()

    # 1. Main Stage Display Window
    stage_disp_idx = int(cfg.get("stage_display", 0))
    stage_disp_name = cfg.get("stage_display_name", "Primary Display")
    stage_disp_x = int(cfg.get("stage_display_x", 0))
    stage_disp_y = int(cfg.get("stage_display_y", 0))
    stage_kiosk = bool(cfg.get("kiosk_mode", False))

    mode_label = "Fullscreen Kiosk" if stage_kiosk else "Windowed"
    print(f"[Launcher] Opening Stage Display on Monitor {stage_disp_idx} ({stage_disp_name}, {mode_label}): {stage_url}")

    firefox_bin = find_system_browser("firefox")
    temp_root = Path(tempfile.gettempdir())
    audio_device = cfg.get("audio_output_device", "default")
    audio_input = cfg.get("audio_input_device", "default")
    env = os.environ.copy()
    if audio_device and audio_device != "default":
        env["PULSE_SINK"] = audio_device
        print(f"[Launcher] 🔊 Routing Stage Audio directly to sink: {audio_device}")
    if audio_input and audio_input != "default":
        env["PULSE_SOURCE"] = audio_input
        print(f"[Launcher] 🎤 Routing Stage Microphone directly to source: {audio_input}")

    stage_launched = False
    if firefox_bin:
        try:
            stage_profile = temp_root / f"tiki_stage_firefox_profile_{stage_disp_idx}"
            configure_firefox_profile(stage_profile)
            cmd = [
                firefox_bin,
                "--no-remote",
                "--profile", str(stage_profile),
                "--new-instance"
            ]
            if stage_kiosk:
                cmd.extend(["--kiosk", f"--kiosk-monitor={stage_disp_idx}"])
            else:
                cmd.append("--new-window")
            cmd.append(stage_url)
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **get_process_group_kwargs())
            launched_browser_procs.append(proc)
            print(f"[Launcher] ✅ Dedicated Stage Window launched via Firefox on monitor {stage_disp_idx} ({stage_disp_name})!")
            stage_launched = True
        except Exception as e:
            print(f"[Launcher] Firefox launch error: {e}")

    if not stage_launched:
        for b_name in ["microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser"]:
            c_bin = find_system_browser(b_name)
            if c_bin:
                try:
                    stage_chrome_data = temp_root / f"tiki_stage_chrome_{stage_disp_idx}"
                    cmd = [
                        c_bin,
                        f"--app={stage_url}",
                        f"--user-data-dir={stage_chrome_data}",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--autoplay-policy=no-user-gesture-required",
                        "--disable-features=PreloadMediaEngagementData",
                        f"--window-position={stage_disp_x},{stage_disp_y}"
                    ]
                    if stage_kiosk:
                        cmd.append("--start-fullscreen")
                    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **get_process_group_kwargs())
                    launched_browser_procs.append(proc)
                    print(f"[Launcher] ✅ Dedicated Stage Window launched via {b_name} on monitor {stage_disp_idx} ({stage_disp_x},{stage_disp_y})!")
                    stage_launched = True
                    break
                except Exception as e:
                    print(f"[Launcher] {b_name} launch error: {e}")

    if not stage_launched:
        try:
            webbrowser.open(stage_url)
            print("[Launcher] Default browser launched for Stage Display.")
        except Exception as e:
            print(f"[Launcher] Could not open browser automatically: {e}")

    # 2. Host DJ Control Window (for multi-display setups)
    host_disp_enabled = bool(cfg.get("host_display_enabled", False))
    if host_disp_enabled and host_url:
        time.sleep(1.0)
        host_disp_idx = int(cfg.get("host_display", 1))
        host_disp_name = cfg.get("host_display_name", f"Monitor {host_disp_idx}")
        host_disp_x = int(cfg.get("host_display_x", 0))
        host_disp_y = int(cfg.get("host_display_y", 0))
        host_kiosk = bool(cfg.get("host_kiosk_mode", False))
        host_mode_label = "Fullscreen Kiosk" if host_kiosk else "App Window"

        print(f"[Launcher] Opening Host DJ Control on Monitor {host_disp_idx} ({host_disp_name}, {host_mode_label}): {host_url}")

        host_launched = False
        if firefox_bin:
            try:
                host_profile = temp_root / f"tiki_host_firefox_profile_{host_disp_idx}"
                configure_firefox_profile(host_profile)
                cmd = [
                    firefox_bin,
                    "--no-remote",
                    "--profile", str(host_profile),
                    "--new-instance"
                ]
                if host_kiosk:
                    cmd.extend(["--kiosk", f"--kiosk-monitor={host_disp_idx}"])
                else:
                    cmd.extend([f"--kiosk-monitor={host_disp_idx}", "--new-window"])
                cmd.append(host_url)
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **get_process_group_kwargs())
                launched_browser_procs.append(proc)
                print(f"[Launcher] ✅ Dedicated Host DJ Window launched via Firefox on monitor {host_disp_idx} ({host_disp_name})!")
                host_launched = True
            except Exception as e:
                print(f"[Launcher] Firefox host launch error: {e}")

        if not host_launched:
            for b_name in ["microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser"]:
                c_bin = find_system_browser(b_name)
                if c_bin:
                    try:
                        host_chrome_data = temp_root / f"tiki_host_chrome_{host_disp_idx}"
                        cmd = [
                            c_bin,
                            f"--app={host_url}",
                            f"--user-data-dir={host_chrome_data}",
                            "--no-first-run",
                            "--no-default-browser-check",
                            f"--window-position={host_disp_x},{host_disp_y}"
                        ]
                        if host_kiosk:
                            cmd.append("--start-fullscreen")
                        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **get_process_group_kwargs())
                        launched_browser_procs.append(proc)
                        print(f"[Launcher] ✅ Dedicated Host DJ Window launched via {b_name} on monitor {host_disp_idx} ({host_disp_x},{host_disp_y})!")
                        host_launched = True
                        break
                    except Exception as e:
                        print(f"[Launcher] {b_name} host launch error: {e}")

def start_cloudflare_quick_tunnel(port: int):
    bin_path = get_bundled_binary_path("cloudflared")
    if not bin_path.exists():
        print("[Tunnel] ⚠️ cloudflared binary not found. Remote tunnel unavailable.")
        return None, None

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
            subprocess.run(["taskkill", "/F", "/IM", "bore.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                subprocess.run(["taskkill", "/F", "/IM", "bore.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

            if needs_restart and not is_shutting_down:
                if active_tunnel_proc:
                    kill_process_tree(active_tunnel_proc)
                    active_tunnel_proc = None

                time.sleep(1.0)
                if mode == "bore":
                    _, active_tunnel_proc = start_permanent_tunnel(local_port, remote_port)
                elif mode in ("cloudflare", "cloudflare_quick"):
                    _, active_tunnel_proc = start_cloudflare_quick_tunnel(local_port)
                elif mode == "cloudflare_named":
                    _, active_tunnel_proc = start_cloudflare_named_tunnel(token, custom_domain)

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

    if not args.no_browser:
        local_base = f"http://127.0.0.1:{target_port}"
        local_stage_url = f"{local_base}/stage?token={HOST_TOKEN}" if HOST_TOKEN else f"{local_base}/stage"
        local_host_url = f"{local_base}/host?token={HOST_TOKEN}" if HOST_TOKEN else f"{local_base}/"
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

    if not args.no_tray:
        tray_urls = {
            "stage": stage_url,
            "host": host_url,
            "join": join_url,
            "setup": f"http://127.0.0.1:{target_port}/setup",
            "public": public_url or join_url,
            "shutdown": f"http://127.0.0.1:{target_port}/api/host/shutdown?token={HOST_TOKEN}" if HOST_TOKEN else f"http://127.0.0.1:{target_port}/api/host/shutdown"
        }
        bar_name = cfg.get("bar_name", "The Tiki Hut Karaoke Lounge")
        start_tray(tray_urls, on_quit_callback=trigger_server_shutdown, bar_name=bar_name)

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
                "stage": stage_url,
                "host": host_url,
                "join": join_url,
                "setup": f"http://127.0.0.1:{target_port}/setup",
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
            hub.start()
        else:
            server.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        cleanup_all_services()
        time.sleep(0.2)
        os._exit(0)

if __name__ == "__main__":
    main()
