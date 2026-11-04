"""
routes/pages.py - HTML page views, QR codes, and web auth routes.

Handles:
- Main singer interface (/, /join)
- Dual-display TV stage screen (/stage)
- Host DJ pairing redirection (/host)
- Table tent card generator (/host/print-tents)
- Fair-use legal and compliance guide (/legal)
- Dynamic QR code generation for singer join, host access, and Wi-Fi hotspot (/qr/*)
- Google OAuth 2.0 flow for YouTube playlist sync (/auth/login, /auth/callback)
- Host PIN authorization (/api/auth/pin)
- Favicon endpoint (/favicon.ico)
"""

import io
import time
from urllib.parse import urlencode

import qrcode
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

import network_helper
from config import (
    BAR_NAME,
    HOST_PIN,
    PORT,
    STATIC_DIR,
    is_configured,
    load_config,
)
from services.network import (
    LOCAL_IP,
    PUBLIC_IP,
    get_base_url,
    get_public_host,
)
from database import get_setting
from services.auth import (
    HOST_TOKEN,
    STAGE_TOKEN,
    PinAuthRequest,
    failed_pin_attempts,
    is_host_request,
    is_local_request,
    is_stage_request,
)
from services.templates import templates
from playlist_sync import exchange_code, get_auth_url

router = APIRouter(tags=["pages"])


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    icon_path = STATIC_DIR / "favicon.ico"
    if icon_path.exists():
        return FileResponse(icon_path, media_type="image/x-icon")
    svg_path = STATIC_DIR / "tiki-icon.svg"
    if svg_path.exists():
        return FileResponse(svg_path, media_type="image/svg+xml")
    return Response(status_code=204)


@router.post("/api/auth/pin")
async def api_auth_pin(req: PinAuthRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = [t for t in failed_pin_attempts.get(client_ip, []) if now - t < 60.0]
    if len(attempts) >= 5:
        failed_pin_attempts[client_ip] = attempts
        raise HTTPException(
            status_code=429,
            detail="Too many failed PIN attempts. Please wait 1 minute before trying again."
        )

    cfg = load_config()
    expected_pin = str(cfg.get("host_pin", HOST_PIN)).strip()
    provided_pin = str(req.pin).strip()

    if provided_pin == expected_pin:
        failed_pin_attempts.pop(client_ip, None)
        response = JSONResponse({"status": "ok", "is_host": True})
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 30, httponly=False, samesite="lax")
        return response
    else:
        attempts.append(now)
        failed_pin_attempts[client_ip] = attempts
        raise HTTPException(status_code=401, detail="Invalid Host PIN. Please check the TV display.")


@router.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    if not is_configured():
        return RedirectResponse(url="/setup")
    pub_h = get_public_host()
    return templates.TemplateResponse(request=request, name="mobile.html", context={
        "bar_name": get_setting("bar_name", "The Tiki Hut Karaoke Lounge"),
        "local_ip": LOCAL_IP,
        "public_ip": PUBLIC_IP,
        "base_url": get_base_url(),
        "display_ip": pub_h if pub_h else f"{PUBLIC_IP}:{PORT}",
        "port": PORT
    })


@router.get("/join", response_class=HTMLResponse)
async def join_page(request: Request):
    if not is_configured():
        return RedirectResponse(url="/setup")
    pub_h = get_public_host()
    return templates.TemplateResponse(request=request, name="mobile.html", context={
        "bar_name": get_setting("bar_name", "The Tiki Hut Karaoke Lounge"),
        "local_ip": LOCAL_IP,
        "public_ip": PUBLIC_IP,
        "base_url": get_base_url(),
        "display_ip": pub_h if pub_h else f"{PUBLIC_IP}:{PORT}",
        "port": PORT
    })


@router.get("/stage", response_class=HTMLResponse)
async def stage_page(request: Request, token: str = ""):
    if not is_configured():
        return RedirectResponse(url="/setup")
    pub_h = get_public_host()
    cfg = load_config()
    pin = str(cfg.get("host_pin", HOST_PIN))
    show_wifi_card = network_helper.is_hotspot_enabled()
    hotspot_ssid = network_helper.get_hotspot_ssid()
    hotspot_password = network_helper.get_hotspot_password()
    response = templates.TemplateResponse(request=request, name="host.html", context={
        "bar_name": get_setting("bar_name", "The Tiki Hut Karaoke Lounge"),
        "local_ip": LOCAL_IP,
        "public_ip": PUBLIC_IP,
        "base_url": get_base_url(),
        "display_ip": pub_h if pub_h else f"{PUBLIC_IP}:{PORT}",
        "port": PORT,
        "host_pin": pin,
        "show_wifi_card": show_wifi_card,
        "hotspot_ssid": hotspot_ssid,
        "hotspot_password": hotspot_password
    })
    response.set_cookie("tiki_stage", STAGE_TOKEN, max_age=86400 * 30, httponly=False)
    if (token and token == HOST_TOKEN) or is_host_request(request) or is_local_request(request):
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 30, httponly=False, samesite="lax")
    return response


@router.get("/host")
async def host_pair_route(request: Request, token: str = ""):
    if (token and token == HOST_TOKEN) or is_host_request(request) or is_local_request(request):
        q_params = dict(request.query_params)
        q_params["tab"] = "host"
        redirect_url = f"/?{urlencode(q_params)}"
        response = RedirectResponse(url=redirect_url)
        response.set_cookie("tiki_host", HOST_TOKEN, max_age=86400 * 7, httponly=False, samesite="lax")
        return response
    return HTMLResponse("<h3>Invalid Host Token!</h3><p>Scan the Host QR code from the main TV screen or enter the Host PIN on the singer screen.</p>", status_code=403)


@router.get("/host/print-tents")
async def print_table_tents(request: Request):
    if not (is_host_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required")
    cfg = load_config()
    default_bar = cfg.get("bar_name") or BAR_NAME or "The Tiki Hut Karaoke Lounge"
    bar_name = get_setting("bar_name", default_bar)
    return templates.TemplateResponse(
        request=request,
        name="print_tents.html",
        context={
            "bar_name": bar_name,
            "base_url": get_base_url(),
            "join_url": f"{get_base_url()}/join"
        }
    )


@router.get("/legal", response_class=HTMLResponse)
async def legal_guide_page(request: Request):
    cfg = load_config()
    default_bar = cfg.get("bar_name") or BAR_NAME or "The Tiki Hut Karaoke Lounge"
    bar_name = get_setting("bar_name", default_bar)
    return templates.TemplateResponse(
        request=request,
        name="legal.html",
        context={
            "bar_name": bar_name,
            "base_url": get_base_url()
        }
    )


@router.get("/qr/public")
async def public_qr():
    url = f"{get_base_url()}/join"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.get("/qr/wifi")
async def wifi_qr():
    ssid = network_helper.get_hotspot_ssid()
    password = network_helper.get_hotspot_password()
    wifi_str = network_helper.format_wifi_qr_string(ssid, password)
    png_bytes = network_helper.generate_qr_png_bytes(wifi_str)
    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")


@router.get("/qr/host")
async def host_qr(request: Request):
    if not (is_host_request(request) or is_stage_request(request) or is_local_request(request)):
        raise HTTPException(status_code=403, detail="Host DJ authorization required to view host QR code.")
    url = f"{get_base_url()}/host?token={HOST_TOKEN}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.get("/auth/login")
async def auth_login(request: Request):
    redirect_uri = f"http://{LOCAL_IP}:{PORT}/auth/callback"
    url = get_auth_url(redirect_uri)
    if not url:
        return HTMLResponse("<h3>Client secrets file not found</h3><p>Place client_secrets.json in the data/ folder to connect your Google account.</p>", status_code=400)
    return RedirectResponse(url)


@router.get("/auth/callback")
async def auth_callback(code: str = ""):
    redirect_uri = f"http://{LOCAL_IP}:{PORT}/auth/callback"
    success = exchange_code(code, redirect_uri)
    if success:
        return HTMLResponse("<h2>Google Account Connected Successfully! 🌺</h2><p>You can close this tab and return to the party.</p>")
    return HTMLResponse("<h2>Authentication failed.</h2>", status_code=400)
