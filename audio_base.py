import logging
from typing import Optional

logger = logging.getLogger("tiki.audio_router")

MONITORING_MODES = ("native", "hardware_direct", "browser")
DEFAULT_MONITORING_MODE = "native"


class BaseAudioRouter:
    """Shared audio monitoring state and mode management for platform routers."""

    def __init__(self):
        self.current_source: str = "default"
        self.current_sink: str = "default"
        self.is_muted: bool = True
        self.volume_pct: int = 80
        self.is_active: bool = False
        self.monitoring_mode: str = DEFAULT_MONITORING_MODE
        self.last_error: Optional[str] = None

    def is_available(self) -> bool:
        return False

    def stop_loopback(self):
        self.is_active = False

    def get_state(self) -> dict:
        return {
            "available": self.is_available(),
            "active": self.is_active,
            "muted": self.is_muted,
            "volume": self.volume_pct,
            "device": self.current_source,
            "monitoring_mode": self.monitoring_mode,
            "last_error": self.last_error,
        }

    def set_monitoring_mode(self, mode: str):
        """Switch monitoring mode. Stops native loopback when switching away from 'native'."""
        if mode not in MONITORING_MODES:
            logger.warning(f"[AudioRouter] Unknown monitoring mode '{mode}', ignoring")
            return
        if mode == self.monitoring_mode:
            return
        self.monitoring_mode = mode
        if mode != "native":
            self.stop_loopback()
        logger.info(f"[AudioRouter] Monitoring mode → {mode}")
