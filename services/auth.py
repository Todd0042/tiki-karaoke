"""
Authentication & Authorization Helpers for Tiki Karaoke.
"""

import secrets
import time
from typing import Dict, List, Optional
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

from config import HOST_TOKEN, load_config
from database import get_setting, set_setting
from services.websocket_manager import manager

STAGE_TOKEN = secrets.token_hex(16)
failed_pin_attempts: Dict[str, List[float]] = {}
failed_room_attempts: Dict[str, List[float]] = {}

_ROOM_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
_PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip")


def is_proxied_request(request: Request) -> bool:
    """Returns True if the request arrived via a reverse proxy / tunnel (e.g. cloudflared)."""
    return any(bool(request.headers.get(h, "").strip()) for h in _PROXY_HEADERS)


def get_client_ip(request: Request) -> str:
    """Resolve the real client IP, honoring reverse-proxy headers only when peer is loopback."""
    peer = request.client.host if request.client else ""
    if peer in _LOOPBACK_HOSTS:
        cf_ip = request.headers.get("cf-connecting-ip", "").strip()
        if cf_ip:
            return cf_ip
        xff = request.headers.get("x-forwarded-for", "").strip()
        if xff:
            return xff.split(",")[0].strip()
        x_real = request.headers.get("x-real-ip", "").strip()
        if x_real:
            return x_real
    return peer or "unknown"


def is_https_request(request: Optional[Request] = None) -> bool:
    """Returns True when the client-facing connection is HTTPS (direct or via proxy)."""
    if request is not None:
        if request.url.scheme == "https":
            return True
        xfp = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        return xfp == "https"
    from services.network import get_base_url
    return get_base_url().startswith("https://")


def set_host_cookie(response: Response, max_age: int = 86400 * 30, request: Optional[Request] = None) -> None:
    """Set the Host DJ session cookie with HttpOnly and conditional Secure flag."""
    response.set_cookie(
        "tiki_host",
        HOST_TOKEN,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=is_https_request(request),
    )


def set_room_cookie(response: Response, code: str, request: Optional[Request] = None) -> None:
    """Persist a verified guest Room Code in an HttpOnly cookie."""
    response.set_cookie(
        "tiki_room",
        code.strip().upper(),
        max_age=86400,
        httponly=True,
        samesite="lax",
        secure=is_https_request(request),
    )


def generate_room_code(length: int = 4) -> str:
    """Generate a short, easy-to-read alphanumeric Room Code."""
    return "".join(secrets.choice(_ROOM_CODE_ALPHABET) for _ in range(length))


def is_room_code_required() -> bool:
    """Returns True if guest Room Code gating is enabled."""
    val = get_setting("require_room_code", "")
    if val != "":
        return val.lower() == "true"
    cfg = load_config()
    return bool(cfg.get("require_room_code", False))


def get_active_room_code() -> str:
    """Return the active Room Code, generating and persisting one if not set yet."""
    code = get_setting("room_code", "").strip().upper()
    if not code:
        code = generate_room_code()
        set_setting("room_code", code)
    return code


def rotate_room_code() -> str:
    """Generate and persist a fresh Room Code, invalidating previous guest codes."""
    code = generate_room_code()
    set_setting("room_code", code)
    return code


def is_host_request(request: Request) -> bool:
    if not HOST_TOKEN:
        return False
    cookie_token = request.cookies.get("tiki_host")
    if cookie_token and secrets.compare_digest(cookie_token, HOST_TOKEN):
        return True
    auth_header = request.headers.get("X-Host-Token")
    if auth_header and secrets.compare_digest(auth_header, HOST_TOKEN):
        return True
    query_token = request.query_params.get("token")
    if query_token and secrets.compare_digest(query_token, HOST_TOKEN):
        return True
    return False


def is_stage_request(request: Request) -> bool:
    cookie_stage = request.cookies.get("tiki_stage")
    if cookie_stage and secrets.compare_digest(cookie_stage, STAGE_TOKEN):
        return True
    stage_header = request.headers.get("X-Stage-Token")
    if stage_header and secrets.compare_digest(stage_header, STAGE_TOKEN):
        return True
    disp_id = request.headers.get("X-Display-Id") or request.query_params.get("display_id")
    if disp_id and disp_id in manager.stage_displays:
        return True
    return False


def is_local_request(request: Request) -> bool:
    """Returns True ONLY for direct (non-proxied) requests from the host machine itself."""
    if not request.client or is_proxied_request(request):
        return False
    return request.client.host in _LOOPBACK_HOSTS


def is_room_authorized(request: Request, explicit_code: str = "") -> bool:
    """Returns True if Room Code gating is disabled, caller is Host/Local/Stage, or code matches."""
    if not is_room_code_required():
        return True
    if is_host_request(request) or is_local_request(request) or is_stage_request(request):
        return True
    expected = get_active_room_code()
    candidate = (
        explicit_code
        or request.query_params.get("room", "")
        or request.headers.get("X-Room-Code", "")
        or request.cookies.get("tiki_room", "")
    ).strip().upper()
    if not candidate or len(candidate) != len(expected):
        return False
    return secrets.compare_digest(candidate, expected)


def require_room_access(request: Request, explicit_code: str = "") -> None:
    """Raise 403 if Room Code gating is active and the caller has not provided the active code."""
    if not is_room_authorized(request, explicit_code=explicit_code):
        raise HTTPException(
            status_code=403,
            detail="Room code required. Scan the TV QR code or enter the 4-character Room Code shown on the Stage screen.",
        )


class PinAuthRequest(BaseModel):
    pin: str


class RoomVerifyRequest(BaseModel):
    code: str
