import io
import os
import re
import socket
import subprocess
import sys
import time
from typing import Dict, List, Tuple
import qrcode
from database import get_setting, set_setting

HOTSPOT_SUBNET_PREFIXES = (
    "10.42.0.",      # Linux NetworkManager Hotspot default
    "192.168.137.",  # Windows Mobile Hotspot default
    "192.168.43.",   # Android Tethering default
    "192.168.49.",   # Wi-Fi Direct SoftAP
    "192.168.12.",   # Alternative Linux/macOS Hotspot
)

VIRTUAL_IFACE_KEYWORDS = (
    "docker", "br-", "veth", "virbr", "vbox", "vmnet", "tun", "tap", "wg"
)

def is_hotspot_ip(ip: str) -> bool:
    """Returns True if the IP address belongs to a known hotspot subnet."""
    if not ip or ip == "127.0.0.1":
        return False
    return any(ip.startswith(prefix) for prefix in HOTSPOT_SUBNET_PREFIXES)

_last_internet_check = 0.0
_cached_internet_available = False
_ipv4_prioritized = False

def prioritize_ipv4() -> None:
    """
    Ensures socket.getaddrinfo returns IPv4 (AF_INET) addresses first.
    Protects against 15-30 second socket timeouts on networks where IPv6 is configured
    on the interface or glibc gai.conf, but upstream IPv6 routing is broken or dropped by ISP.
    """
    global _ipv4_prioritized
    if _ipv4_prioritized:
        return
    orig_getaddrinfo = socket.getaddrinfo

    def getaddrinfo_ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
        res = orig_getaddrinfo(host, port, family, type, proto, flags)
        if family == 0 and res:  # AF_UNSPEC: sort IPv4 (AF_INET = 2) ahead of IPv6 (AF_INET6 = 10)
            return sorted(res, key=lambda x: 0 if x[0] == socket.AF_INET else 1)
        return res

    socket.getaddrinfo = getaddrinfo_ipv4_first
    _ipv4_prioritized = True

# Automatically apply IPv4 prioritization on load
prioritize_ipv4()

def is_internet_available(timeout: float = 1.2, force_recheck: bool = False) -> bool:
    """
    Checks if active internet route exists by probing DNS resolution and HTTPS roots.
    Caches result for 8 seconds to prevent socket saturation during live typing.
    Never uses TCP port 53 (which is blocked by most home routers and firewalls).
    """
    global _last_internet_check, _cached_internet_available
    now = time.time()
    if not force_recheck and (now - _last_internet_check < 8.0):
        return _cached_internet_available

    # 1. Ultra-fast DNS resolution probe (1-5ms on standard Wi-Fi/LAN)
    try:
        res = socket.getaddrinfo("www.google.com", 443, socket.AF_INET, socket.SOCK_STREAM)
        if res:
            _cached_internet_available = True
            _last_internet_check = now
            return True
    except Exception:
        pass

    # 2. Universal HTTPS port 443 TCP probe across Cloudflare, Google, and OpenDNS
    # Port 443 is universally permitted across consumer routers, public Wi-Fi, and firewalls
    for host in ("1.1.1.1", "8.8.8.8", "208.67.222.222"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, 443))
            s.close()
            _cached_internet_available = True
            _last_internet_check = now
            return True
        except Exception:
            pass

    _cached_internet_available = False
    _last_internet_check = now
    return False

def get_interface_ips() -> List[Tuple[str, str]]:
    """
    Returns list of (interface_name, ip_address) detected across network adapters.
    Works across Linux, Windows, and macOS.
    """
    results: List[Tuple[str, str]] = []

    # 1. Linux via `ip -4 -o addr`
    if sys.platform.startswith("linux"):
        try:
            out = subprocess.check_output(
                ["ip", "-4", "-o", "addr"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1.5
            )
            for line in out.strip().splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    iface = parts[1]
                    ip = parts[3].split("/")[0]
                    if ip and not ip.startswith("127."):
                        results.append((iface, ip))
            if results:
                return results
        except Exception:
            pass

    # 2. Windows via `ipconfig`
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["ipconfig"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2.0
            )
            current_adapter = "Adapter"
            for line in out.splitlines():
                line = line.strip()
                if line.endswith(":"):
                    current_adapter = line[:-1]
                elif "IPv4 Address" in line or "IP Address" in line:
                    match = re.search(r":\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", line)
                    if match:
                        ip = match.group(1).strip()
                        if ip and not ip.startswith("127."):
                            results.append((current_adapter, ip))
            if results:
                return results
        except Exception:
            pass

    # 3. Universal UDP Socket Probes across common subnets
    probes = [
        "8.8.8.8",
        "1.1.1.1",
        "10.42.0.1",
        "192.168.137.1",
        "192.168.43.1",
        "192.168.1.1",
        "192.168.0.1",
        "10.0.0.1"
    ]
    seen_ips = set()
    for probe in probes:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.2)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127.") and ip not in seen_ips:
                seen_ips.add(ip)
                results.append(("adapter", ip))
        except Exception:
            pass

    return results

def get_best_local_ip() -> str:
    """
    Selects the best IPv4 address for connected party singers:
    1. Hotspot Subnet IP (e.g. 10.42.0.1 or 192.168.137.1)
    2. Wi-Fi / Physical LAN IP (e.g. wlan0, eth0, Wi-Fi)
    3. Any non-virtual adapter IP
    4. 127.0.0.1 fallback
    """
    interfaces = get_interface_ips()

    # Step 1: Hotspot IP takes top precedence
    for iface, ip in interfaces:
        if is_hotspot_ip(ip):
            return ip

    # Step 2: Physical Wi-Fi or LAN (excluding virtual Docker / VM bridges)
    for iface, ip in interfaces:
        lower_iface = iface.lower()
        if any(kw in lower_iface for kw in VIRTUAL_IFACE_KEYWORDS):
            continue
        if any(kw in lower_iface for kw in ("wlan", "wi-fi", "wifi", "eth", "en", "wl")):
            return ip

    # Step 3: Any non-virtual adapter
    for iface, ip in interfaces:
        lower_iface = iface.lower()
        if not any(kw in lower_iface for kw in VIRTUAL_IFACE_KEYWORDS):
            return ip

    # Step 4: First detected IP or loopback
    if interfaces:
        return interfaces[0][1]

    return "127.0.0.1"

def get_hotspot_ssid() -> str:
    """Returns persisted or default Hotspot SSID name."""
    val = get_setting("hotspot_ssid", None)
    if val is not None:
        return val.strip()
    from config import load_config
    cfg = load_config()
    return str(cfg.get("hotspot_ssid", "")).strip()

def get_hotspot_password() -> str:
    """Returns persisted or default Hotspot WPA2/WPA3 password."""
    val = get_setting("hotspot_password", None)
    if val is not None:
        return val.strip()
    from config import load_config
    cfg = load_config()
    return str(cfg.get("hotspot_password", "")).strip()

def is_hotspot_enabled() -> bool:
    """Returns True only if local Wi-Fi mode is active AND hotspot broadcast is explicitly enabled with a non-empty SSID."""
    from config import load_config
    cfg = load_config()
    remote_mode = get_setting("remote_mode", cfg.get("remote_mode", "cloudflare_quick"))
    if remote_mode != "local":
        return False
    enabled = get_setting("hotspot_enabled", "true" if cfg.get("hotspot_enabled") else "false")
    if enabled != "true":
        return False
    ssid = get_hotspot_ssid()
    return bool(ssid)

def save_hotspot_config(ssid: str, password: str, enabled: bool = True):
    """Saves customized Hotspot credentials and enabled state."""
    from config import save_config
    clean_ssid = ssid.strip()
    clean_pass = password.strip()
    set_setting("hotspot_ssid", clean_ssid)
    set_setting("hotspot_password", clean_pass)
    set_setting("hotspot_enabled", "true" if enabled else "false")
    save_config({
        "hotspot_ssid": clean_ssid,
        "hotspot_password": clean_pass,
        "hotspot_enabled": enabled
    })

def format_wifi_qr_string(ssid: str, password: str) -> str:
    """
    Formats standard Wi-Fi QR code connection payload.
    When scanned by iOS or Android camera, connects automatically.
    """
    return f"WIFI:S:{ssid};T:WPA;P:{password};;"

def generate_qr_png_bytes(content: str) -> bytes:
    """Renders a high-contrast QR code as PNG image bytes."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()
