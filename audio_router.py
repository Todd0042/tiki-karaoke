import sys

from audio_router_linux import (
    DEFAULT_MONITORING_MODE,
    MONITORING_MODES,
    NativeAudioRouter,
)
from audio_router_win import MIC_ROUTER_HELPER, WasapiAudioRouter

# Platform factory: Windows uses the WASAPI helper; everything else keeps
# the pactl/PipeWire path. Same public interface either way.
if sys.platform == "win32":
    audio_router = WasapiAudioRouter()
else:
    audio_router = NativeAudioRouter()


def set_monitoring_mode(mode: str):
    """Module-level forwarder for audio_router.set_monitoring_mode."""
    return audio_router.set_monitoring_mode(mode)


def get_state() -> dict:
    """Module-level forwarder for audio_router.get_state."""
    return audio_router.get_state()


__all__ = [
    "DEFAULT_MONITORING_MODE",
    "MIC_ROUTER_HELPER",
    "MONITORING_MODES",
    "NativeAudioRouter",
    "WasapiAudioRouter",
    "audio_router",
    "get_state",
    "set_monitoring_mode",
]
