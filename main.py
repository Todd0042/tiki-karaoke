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
from pathlib import Path

import shutil

# Ensure project root is in python path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import (
    DATA_DIR, HOST_TOKEN, HOST_PIN, PORT, TARGET_LUFS,
    load_config, is_configured
)
from app import LOCAL_IP, PUBLIC_IP, get_base_url, get_public_host

TIKI_BANNER = r"""
  ______ _            _     _         _______ _    _ 
 |  ____| |          (_)   | |       |__   __(_)  | |
 | |__  | | ___  _ __ _  __| | __ _     | |   _   | | _____  
 |  __| | |/ _ \| '__| |/ _` |/ _` |    | |  | |  | |/ / _ \ 
 | |    | | (_) | |  | | (_| | (_| |    | |  | |  |   <  __/ 
 |_|    |_|\___/|_|  |_|\__,_|\__,_|    |_|  |_|  |_|\_\___| 
      🌴 🌺  KARAOKE LOUNGE & NEON BAR  🍹 🌴
"""

PID_FILE = DATA_DIR / "karaoke.pid"
active_tunnel_proc = None
is_shutting_down = False

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
    """Ensures all spawned background processes and tunnels are cleanly terminated."""
    global active_tunnel_proc, is_shutting_down
    if is_shutting_down:
        return
    is_shutting_down = True

    print("\n🌴 [Shutdown] Gracefully shutting down Florida Tiki Karaoke Lounge...")

    if active_tunnel_proc and active_tunnel_proc.poll() is None:
        print("[Shutdown] Stopping remote tunnel process...")
        try:
            # Terminate the entire process group
            os.killpg(os.getpgid(active_tunnel_proc.pid), signal.SIGTERM)
            active_tunnel_proc.wait(timeout=2.0)
        except Exception:
            try:
                os.killpg(os.getpgid(active_tunnel_proc.pid), signal.SIGKILL)
            except Exception:
                pass
        active_tunnel_proc = None

    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except Exception:
            pass

    print("✅ [Shutdown] All services stopped cleanly. Aloha! 🌺\n")

def handle_signal(sig, frame):
    cleanup_all_services()
    sys.exit(0)

# Register clean shutdown hooks
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
atexit.register(cleanup_all_services)

def open_browser_delayed(url: str, delay: float = 1.5):
    time.sleep(delay)
    cfg = load_config()
    target_disp_idx = int(cfg.get("stage_display", 0))
    disp_name = cfg.get("stage_display_name", "Primary Display")
    disp_x = int(cfg.get("stage_display_x", 0))
    disp_y = int(cfg.get("stage_display_y", 0))
    kiosk = bool(cfg.get("kiosk_mode", True))

    mode_label = "Fullscreen Kiosk" if kiosk else "Windowed"
    print(f"[Launcher] Opening Stage Display on Monitor {target_disp_idx} ({disp_name}, {mode_label}): {url}")

    # 1. Try Firefox (supports --kiosk --kiosk-monitor=<n>)
    firefox_bin = shutil.which("firefox")
    if firefox_bin:
        try:
            # Firefox kiosk-monitor is 1-indexed (monitor 0 -> 1)
            cmd = [firefox_bin, "--new-instance"]
            if kiosk:
                cmd.extend(["--kiosk", f"--kiosk-monitor={target_disp_idx + 1}"])
            else:
                cmd.append("--new-window")
            cmd.append(url)
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
            print(f"[Launcher] ✅ Dedicated Stage Window launched via Firefox on monitor {target_disp_idx}!")
            return
        except Exception as e:
            print(f"[Launcher] Firefox launch error: {e}")

    # 2. Try Chrome / Chromium / Brave / Edge (supports --app and --window-position)
    for b_name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"]:
        c_bin = shutil.which(b_name)
        if c_bin:
            try:
                cmd = [c_bin, f"--app={url}", f"--window-position={disp_x},{disp_y}"]
                if kiosk:
                    cmd.append("--start-fullscreen")
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
                print(f"[Launcher] ✅ Dedicated Stage Window launched via {b_name} on monitor {target_disp_idx} ({disp_x},{disp_y})!")
                return
            except Exception as e:
                print(f"[Launcher] {b_name} launch error: {e}")

    # 3. Fallback to default system browser
    try:
        webbrowser.open(url)
        print("[Launcher] Default browser launched.")
    except Exception as e:
        print(f"[Launcher] Could not open browser automatically: {e}")

def start_cloudflare_tunnel(port: int):
    bin_path = BASE_DIR / "bin" / "cloudflared"
    if not bin_path.exists():
        print("[Tunnel] cloudflared binary not found in bin/")
        return None, None

    print("[Tunnel] Establishing secure Cloudflare Tunnel...")
    proc = subprocess.Popen(
        [str(bin_path), "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        preexec_fn=os.setsid
    )
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

def start_permanent_tunnel(port: int, remote_port: int = 38480):
    global active_tunnel_proc
    bin_path = BASE_DIR / "bin" / "bore"
    if not bin_path.exists():
        print("[Remote] bore binary not found in bin/")
        return None, None

    print(f"[Remote] Establishing permanent static tunnel on bore.pub:{remote_port}...")
    proc = subprocess.Popen(
        [str(bin_path), "local", str(port), "--to", "bore.pub", "--port", str(remote_port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid
    )
    active_tunnel_proc = proc

    # Read output in background to prevent pipe buffer from filling up
    def _drain(pipe):
        for line in iter(pipe.readline, ''):
            if "error" in line.lower():
                print(f"[Remote Error] {line.strip()}")
    threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()

    time.sleep(1.2)
    url = f"http://bore.pub:{remote_port}"
    os.environ["TIKI_PUBLIC_HOST"] = f"bore.pub:{remote_port}"
    return url, proc

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

    parser = argparse.ArgumentParser(description="Florida Tiki Karaoke Lounge Full Application Orchestrator")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(cfg.get("port", PORT)), help=f"Port to run on (default: {cfg.get('port', PORT)})")
    parser.add_argument("--remote-port", type=int, default=int(cfg.get("remote_port", 38480)), help="Permanent public port on bore.pub (default: 38480)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch browser on start")
    parser.add_argument("--local-only", action="store_true", help="Run on local network only (disable remote tunnels)")
    parser.add_argument("--tunnel", action="store_true", help="Force ephemeral Cloudflare HTTPS tunnel instead of permanent static URL")
    args = parser.parse_args()

    # Write PID file for process tracking
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        print(f"[Warning] Could not write PID file: {e}")

    # Check port availability
    if not check_port_available(args.host, args.port):
        print(f"\n❌ [Error] Port {args.port} is already in use by another process!")
        print("Run './start.sh --stop' to terminate existing instances, or specify a different port with '--port'.\n")
        sys.exit(1)

    remote_mode = cfg.get("remote_mode", "bore")
    if args.local_only:
        remote_mode = "local"
    elif args.tunnel:
        remote_mode = "cloudflare"

    public_url = None

    if remote_mode == "cloudflare":
        public_url, active_tunnel_proc = start_cloudflare_tunnel(args.port)
        if public_url:
            print(f"🌐 [Tunnel Active] Temporary Cloudflare URL: {public_url}")
            stage_url = f"{public_url}/stage"
            join_url = f"{public_url}/join"
            host_url = f"{public_url}/host?token={HOST_TOKEN}"
        else:
            stage_url = f"{get_base_url()}/stage"
            join_url = f"{get_base_url()}/join"
            host_url = f"{get_base_url()}/host?token={HOST_TOKEN}"
    elif remote_mode == "bore":
        public_url, active_tunnel_proc = start_permanent_tunnel(args.port, args.remote_port)
        if public_url:
            print(f"🌐 [Permanent Fixed URL Active] Static Public URL: {public_url}")
            stage_url = f"{public_url}/stage"
            join_url = f"{public_url}/join"
            host_url = f"{public_url}/host?token={HOST_TOKEN}"
        else:
            stage_url = f"{get_base_url()}/stage"
            join_url = f"{get_base_url()}/join"
            host_url = f"{get_base_url()}/host?token={HOST_TOKEN}"
    elif remote_mode == "custom" and cfg.get("public_host"):
        os.environ["TIKI_PUBLIC_HOST"] = cfg["public_host"]
        stage_url = f"{get_base_url()}/stage"
        join_url = f"{get_base_url()}/join"
        host_url = f"{get_base_url()}/host?token={HOST_TOKEN}"
    else:
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

    if not args.no_browser:
        threading.Thread(target=open_browser_delayed, args=(stage_url,), daemon=True).start()

    try:
        import uvicorn
        uvicorn_config = uvicorn.Config(
            "app:app",
            host=args.host,
            port=args.port,
            log_level="info",
            loop="asyncio"
        )
        server = uvicorn.Server(uvicorn_config)
        server.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        cleanup_all_services()

if __name__ == "__main__":
    main()
