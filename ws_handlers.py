import logging
import time
from typing import Any, Callable, Coroutine, Dict, Optional

from fastapi import WebSocket
from pydantic import BaseModel, Field, ValidationError

from audio_router import MONITORING_MODES, audio_router
from config import DEFAULT_INTERMISSION_SECONDS
from database import (
    get_current_playing,
    get_pending_downloads,
    get_queue,
    get_setting,
    set_setting,
)
from services.websocket_manager import manager

logger = logging.getLogger("tiki.ws_handlers")


class StageRegisterPayload(BaseModel):
    display_id: Optional[str] = None
    name: str = "Stage Display"
    is_muted: bool = False


class StageAudioStatePayload(BaseModel):
    display_id: str = Field(..., min_length=1)
    is_muted: bool = False


class EmojiReactionPayload(BaseModel):
    emoji: str = "🌺"


class SingerJoinedPayload(BaseModel):
    name: str = "Guest Singer"
    avatar: str = "🌴"


class SoundboardPlayPayload(BaseModel):
    sound: str = "applause"


class VolumeChangePayload(BaseModel):
    volume: float = Field(1.0, ge=0.0, le=2.0)


class PitchChangePayload(BaseModel):
    pitch: int = Field(0, ge=-12, le=12)


class HostMicSettingsPayload(BaseModel):
    muted: Optional[bool] = None
    volume: Optional[float] = None
    device: Optional[str] = None
    monitoring_mode: Optional[str] = None
    reverb: Optional[bool] = None
    echo_cancellation: Optional[bool] = None
    ducking_enabled: Optional[bool] = None
    ducking_db: Optional[float] = None
    cdg_theme: Optional[str] = None
    cdg_glow: Optional[bool] = None


def _parse_payload(model_cls: type[BaseModel], data: Dict[str, Any]) -> Optional[BaseModel]:
    try:
        return model_cls.model_validate(data)
    except ValidationError:
        return None


async def handle_ping(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    await websocket.send_json({"type": "pong", "time": time.time()})


async def handle_get_pending_downloads(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    await websocket.send_json({
        "type": "pending_downloads_updated",
        "pending_downloads": get_pending_downloads(),
    })


async def handle_stage_register(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    payload = _parse_payload(StageRegisterPayload, data)
    if payload is None:
        await websocket.send_json({"type": "ws_error", "message": "Invalid stage_register payload"})
        return

    manager.register_stage_display(
        websocket=websocket,
        display_id=payload.display_id or f"disp_{id(websocket)}",
        name=payload.name,
        is_muted=bool(payload.is_muted),
        ip=websocket.client.host if websocket.client else "",
    )
    await manager.broadcast({
        "type": "displays_updated",
        "displays": manager.get_stage_displays(),
    })

    current_playing = get_current_playing()
    queue = get_queue()
    pending = [q for q in queue if q["status"] == "pending"]
    if not current_playing and pending and not state.get("current_intermission_end"):
        dur = int(get_setting("intermission_seconds", str(DEFAULT_INTERMISSION_SECONDS)))
        state["current_intermission_end"] = time.time() + dur
        await manager.broadcast({
            "type": "start_intermission",
            "next_track": pending[0],
            "duration": dur,
        })


async def handle_stage_audio_state(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    payload = _parse_payload(StageAudioStatePayload, data)
    if payload and manager.is_display_authorized(websocket, payload.display_id):
        manager.set_display_muted(payload.display_id, bool(payload.is_muted))
        await manager.broadcast({
            "type": "displays_updated",
            "displays": manager.get_stage_displays(),
        })


async def handle_emoji_reaction(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    payload = _parse_payload(EmojiReactionPayload, data)
    if payload and manager.check_emoji_rate_limit(websocket):
        await manager.broadcast({
            "type": "emoji_reaction",
            "emoji": payload.emoji,
        })


async def handle_singer_joined(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    payload = _parse_payload(SingerJoinedPayload, data)
    if not payload:
        return
    name = payload.name.strip()
    avatar = payload.avatar.strip() or "🌴"
    if name:
        await manager.broadcast({
            "type": "singer_joined",
            "name": name,
            "avatar": avatar,
        })


async def handle_soundboard_play(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    if websocket not in manager.host_connections:
        return
    payload = _parse_payload(SoundboardPlayPayload, data)
    if payload:
        await manager.broadcast({
            "type": "soundboard_play",
            "sound": payload.sound,
        })


async def handle_volume_change(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    if websocket not in manager.host_connections:
        return
    payload = _parse_payload(VolumeChangePayload, data)
    if payload is None:
        await websocket.send_json({"type": "host_alert", "message": "Invalid volume value"})
        return
    await manager.broadcast({"type": "volume_change", "volume": payload.volume})


async def handle_pitch_change(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    if websocket not in manager.host_connections:
        return
    payload = _parse_payload(PitchChangePayload, data)
    if payload is None:
        await websocket.send_json({"type": "host_alert", "message": "Invalid pitch value"})
        return
    await manager.broadcast({"type": "pitch_change", "pitch": payload.pitch})


def _build_host_mic_state_message() -> Dict[str, Any]:
    st = audio_router.get_state()
    try:
        duck_db_val = int(float(get_setting("mic_ducking_db", "-6")))
    except (ValueError, TypeError) as e:
        logger.warning(f"[WS] Corrupt stored mic_ducking_db setting, defaulting to -6: {e}")
        duck_db_val = -6
    return {
        "type": "host_mic_settings",
        "volume": st["volume"],
        "muted": st["muted"],
        "device": st["device"],
        "available": st.get("available", False),
        "latency_ms": st.get("latency_ms_est"),
        "monitoring_mode": get_setting("mic_monitoring_mode", st.get("monitoring_mode", "native")),
        "reverb": get_setting("mic_reverb", "true").lower() == "true",
        "echo_cancellation": get_setting("mic_echo_cancellation", "false").lower() == "true",
        "ducking_enabled": get_setting("mic_ducking_enabled", "true").lower() == "true",
        "ducking_db": duck_db_val,
        "cdg_theme": get_setting("cdg_theme", "neon"),
        "cdg_glow": get_setting("cdg_glow", "true").lower() == "true",
        "audio_error": st.get("last_error"),
    }


async def handle_host_mic_settings(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    if websocket not in manager.host_connections:
        return

    muted = data.get("muted")
    volume = data.get("volume")
    device = data.get("device")
    monitoring_mode = data.get("monitoring_mode")

    if muted is not None:
        audio_router.set_muted(bool(muted))
    if volume is not None:
        try:
            audio_router.set_volume(int(float(volume)))
        except (ValueError, TypeError) as e:
            logger.warning(f"[WS] Invalid mic volume '{volume}': {e}")
            await websocket.send_json({"type": "host_alert", "message": f"Invalid mic volume: {volume}"})
    if device is not None and str(device).strip():
        audio_router.set_device(str(device).strip())
    if monitoring_mode is not None:
        if monitoring_mode in MONITORING_MODES:
            audio_router.set_monitoring_mode(monitoring_mode)
            set_setting("mic_monitoring_mode", monitoring_mode)
            if monitoring_mode == "native" and not audio_router.is_muted and not audio_router.start_loopback():
                logger.warning("[WS] Native mic loopback failed to start")
                await websocket.send_json({
                    "type": "host_alert",
                    "message": "Native mic loopback could not start. Check your audio device.",
                })
        else:
            logger.warning(f"[WS] Unknown monitoring_mode '{monitoring_mode}'")
            await websocket.send_json({
                "type": "host_alert",
                "message": f"Unsupported monitoring mode: {monitoring_mode}",
            })

    if data.get("reverb") is not None:
        set_setting("mic_reverb", "true" if bool(data["reverb"]) else "false")
    if data.get("echo_cancellation") is not None:
        set_setting("mic_echo_cancellation", "true" if bool(data["echo_cancellation"]) else "false")
    if data.get("ducking_enabled") is not None:
        set_setting("mic_ducking_enabled", "true" if bool(data["ducking_enabled"]) else "false")
    if data.get("ducking_db") is not None:
        try:
            set_setting("mic_ducking_db", str(int(float(data["ducking_db"]))))
        except (ValueError, TypeError) as e:
            logger.warning(f"[WS] Invalid ducking_db '{data['ducking_db']}': {e}")
            await websocket.send_json({
                "type": "host_alert",
                "message": f"Invalid ducking dB value: {data['ducking_db']}",
            })
    if data.get("cdg_theme"):
        set_setting("cdg_theme", str(data["cdg_theme"]).strip())
    if data.get("cdg_glow") is not None:
        set_setting("cdg_glow", "true" if bool(data["cdg_glow"]) else "false")

    await manager.broadcast(_build_host_mic_state_message())


WsHandler = Callable[[WebSocket, Dict[str, Any], Dict[str, Any]], Coroutine[Any, Any, None]]

WS_HANDLERS: Dict[str, WsHandler] = {
    "ping": handle_ping,
    "get_pending_downloads": handle_get_pending_downloads,
    "stage_register": handle_stage_register,
    "stage_audio_state": handle_stage_audio_state,
    "emoji_reaction": handle_emoji_reaction,
    "singer_joined": handle_singer_joined,
    "soundboard_play": handle_soundboard_play,
    "volume_change": handle_volume_change,
    "pitch_change": handle_pitch_change,
    "host_mic_settings": handle_host_mic_settings,
}


async def dispatch_ws_message(websocket: WebSocket, data: Dict[str, Any], state: Dict[str, Any]) -> None:
    msg_type = data.get("type")
    if not isinstance(msg_type, str) or not msg_type.strip():
        logger.warning("[WS] Missing or non-string message 'type'")
        await websocket.send_json({"type": "ws_error", "message": "Missing message type"})
        return

    handler = WS_HANDLERS.get(msg_type)
    if handler is None:
        logger.debug(f"[WS] Unknown message type '{msg_type}'")
        await websocket.send_json({"type": "ws_error", "message": f"Unknown message type: {msg_type}"})
        return

    await handler(websocket, data, state)
