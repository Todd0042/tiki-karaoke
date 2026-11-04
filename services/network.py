"""
services/network.py - Network identification and URL helpers for Tiki Karaoke.

Determines the best local LAN IP, public host/tunnel domain, and full base URLs.
"""

import os
import socket
from config import PUBLIC_HOST, REMOTE_MODE, PORT, load_config
import network_helper


def get_local_ip() -> str:
    try:
        return network_helper.get_best_local_ip()
    except Exception:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip


def get_public_host() -> str:
    return os.getenv("TIKI_PUBLIC_HOST", "").strip() or PUBLIC_HOST


def get_public_ip() -> str:
    host = get_public_host()
    if host:
        return host
    return get_local_ip()


LOCAL_IP = get_local_ip()
PUBLIC_IP = get_public_ip()


def get_base_url() -> str:
    cfg = load_config()
    mode = cfg.get("remote_mode", REMOTE_MODE)
    host = get_public_host()
    if mode != "local" and host:
        if host.startswith("http://") or host.startswith("https://"):
            return host
        if os.getenv("TIKI_USE_HTTPS") == "1" or "trycloudflare.com" in host or ".ts.net" in host:
            return f"https://{host}"
        return f"http://{host}"
    return f"http://{get_local_ip()}:{PORT}"
