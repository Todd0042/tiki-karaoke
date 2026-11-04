"""
Authentication & Authorization Helpers for Tiki Karaoke.
"""

import time
import secrets
from typing import Dict, List, Optional
from fastapi import Request
from pydantic import BaseModel

from config import HOST_TOKEN
from services.websocket_manager import manager

STAGE_TOKEN = secrets.token_hex(16)
failed_pin_attempts: Dict[str, List[float]] = {}


def is_host_request(request: Request) -> bool:
    if not HOST_TOKEN:
        return False
    cookie_token = request.cookies.get("tiki_host")
    if cookie_token == HOST_TOKEN:
        return True
    auth_header = request.headers.get("X-Host-Token")
    if auth_header == HOST_TOKEN:
        return True
    query_token = request.query_params.get("token")
    if query_token == HOST_TOKEN:
        return True
    return False


def is_stage_request(request: Request) -> bool:
    cookie_stage = request.cookies.get("tiki_stage")
    if cookie_stage and cookie_stage == STAGE_TOKEN:
        return True
    stage_header = request.headers.get("X-Stage-Token")
    if stage_header and stage_header == STAGE_TOKEN:
        return True
    disp_id = request.headers.get("X-Display-Id") or request.query_params.get("display_id")
    if disp_id and disp_id in manager.stage_displays:
        return True
    return False


def is_local_request(request: Request) -> bool:
    """Returns True if the request originates from localhost or the host machine itself."""
    if not request.client:
        return False
    h = request.client.host
    return h in ("127.0.0.1", "::1", "localhost")


class PinAuthRequest(BaseModel):
    pin: str
