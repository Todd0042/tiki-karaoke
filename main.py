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

    print("\n🌴 [Shutdown] Gracefully shutting down Tiki Karaoke Lounge...")

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
    ]
    try:
        user_js.write_text("\n".join(prefs) + "\n")
    except Exception as e:
        print(f"[Launcher] Warning: Could not write Firefox user.js prefs: {e}")

def open_browser_delayed(stage_url: str, host_url: str = "", delay: float = 1.5):
    time.sleep(delay)
    cfg = load_config()

    # 1. Main Stage Display Window
    stage_disp_idx = int(cfg.get("stage_display", 0))
    stage_disp_name = cfg.get("stage_display_name", "Primary Display")
    stage_disp_x = int(cfg.get("stage_display_x", 0))
    stage_disp_y = int(cfg.get("stage_display_y", 0))
    stage_kiosk = bool(cfg.get("kiosk_mode", True))

    mode_label = "Fullscreen Kiosk" if stage_kiosk else "Windowed"
    print(f"[Launcher] Opening Stage Display on Monitor {stage_disp_idx} ({stage_disp_name}, {mode_label}): {stage_url}")

    firefox_bin = shutil.which("firefox")
    audio_device = cfg.get("audio_output_device", "default")
    env = os.environ.copy()
    if audio_device and audio_device != "default":
        env["PULSE_SINK"] = audio_device
        print(f"[Launcher] 🔊 Routing Stage Audio directly to sink: {audio_device}")

    stage_launched = False
    if firefox_bin:
        try:
            stage_profile = Path(f"/tmp/tiki_stage_firefox_profile_{stage_disp_idx}")
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
            subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
            print(f"[Launcher] ✅ Dedicated Stage Window launched via Firefox on monitor {stage_disp_idx} ({stage_disp_name})!")
            stage_launched = True
        except Exception as e:
            print(f"[Launcher] Firefox launch error: {e}")

    if not stage_launched:
        for b_name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"]:
            c_bin = shutil.which(b_name)
            if c_bin:
                try:
                    cmd = [c_bin, f"--app={stage_url}", f"--window-position={stage_disp_x},{stage_disp_y}"]
                    if stage_kiosk:
                        cmd.append("--start-fullscreen")
                    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
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
                host_profile = Path(f"/tmp/tiki_host_firefox_profile_{host_disp_idx}")
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
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
                print(f"[Launcher] ✅ Dedicated Host DJ Window launched via Firefox on monitor {host_disp_idx} ({host_disp_name})!")
                host_launched = True
            except Exception as e:
                print(f"[Launcher] Firefox host launch error: {e}")

        if not host_launched:
            for b_name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"]:
                c_bin = shutil.which(b_name)
                if c_bin:
                    try:
                        cmd = [c_bin, f"--app={host_url}", f"--window-position={host_disp_x},{host_disp_y}"]
                        if host_kiosk:
                            cmd.append("--start-fullscreen")
                        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
                        print(f"[Launcher] ✅ Dedicated Host DJ Window launched via {b_name} on monitor {host_disp_idx} ({host_disp_x},{host_disp_y})!")
                        host_launched = True
                        break
                    except Exception as e:
                        print(f"[Launcher] {b_name} host launch error: {e}")

def start_cloudflare_quick_tunnel(port: int):
    bin_path = BASE_DIR / "bin" / "cloudflared"
    if not bin_path.exists():
        print("[Tunnel] ⚠️ cloudflared binary not found in bin/. Remote tunnel unavailable.")
        return None, None

    print("[Tunnel] 🔒 Establishing secure Cloudflare Quick Tunnel (Trusted HTTPS)...")
    try:
        proc = subprocess.Popen(
            [str(bin_path), "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid
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
    bin_path = BASE_DIR / "bin" / "cloudflared"
    if not bin_path.exists():
        print("[Tunnel] ⚠️ cloudflared binary not found in bin/")
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
            preexec_fn=os.setsid
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
    bin_path = BASE_DIR / "bin" / "bore"
    if not bin_path.exists():
        print("[Remote] bore binary not found in bin/")
        return None, None

    # Kill any stale local bore processes to free up remote static port
    try:
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
        preexec_fn=os.setsid
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
            subprocess.run(["pkill", "-9", "-f", "bore local"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            proc = subprocess.Popen(
                [str(bin_path), "local", str(port), "--to", "bore.pub", "--port", str(remote_port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid
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
                    try:
                        os.killpg(os.getpgid(active_tunnel_proc.pid), signal.SIGKILL)
                    except Exception:
                        pass
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
    parser.add_argument("--local-only", action="store_true", help="Run on local network only (disable remote tunnels)")
    parser.add_argument("--tunnel", action="store_true", help="Force ephemeral Cloudflare HTTPS tunnel (Trusted SSL)")
    parser.add_argument("--bore", action="store_true", help="Force legacy Bore static port tunnel (HTTP)")
    args = parser.parse_args()

    # Write PID file for process tracking
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        print(f"[Warning] Could not write PID file: {e}")

    # 1. Pre-flight dependency check (FFmpeg / FFprobe)
    ffmpeg_ok = bool(shutil.which("ffmpeg"))
    if not ffmpeg_ok:
        print("\n" + "!" * 68)
        print("⚠️  [NOTICE] FFmpeg is not installed on this system.")
        print("   YouTube song downloading and audio extraction will be unavailable.")
        print("   To enable full functionality, install FFmpeg:")
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
        server.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        cleanup_all_services()

if __name__ == "__main__":
    main()
