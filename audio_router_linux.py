import logging
import shutil
import subprocess
from typing import Optional

from audio_base import BaseAudioRouter, DEFAULT_MONITORING_MODE, MONITORING_MODES
from config import load_config

logger = logging.getLogger("tiki.audio_router")


class NativeAudioRouter(BaseAudioRouter):
    """
    Manages low-latency native hardware microphone loopback via PipeWire / PulseAudio.
    Achieves sub-2ms vocal round-trip latency by bypassing user-space browser WebRTC audio.
    """

    def __init__(self):
        super().__init__()
        self.module_id: Optional[str] = None

    def is_available(self) -> bool:
        return bool(shutil.which("pactl"))

    def get_loopback_sink_input_id(self) -> Optional[str]:
        if not self.is_available():
            return None
        try:
            res = subprocess.run(
                ["pactl", "list", "sink-inputs"],
                capture_output=True,
                text=True,
                timeout=1.5
            )
            if res.returncode == 0:
                blocks = res.stdout.split("Sink Input #")
                for b in blocks[1:]:
                    if "TikiKaraoke_Mic" in b:
                        lines = b.splitlines()
                        if lines:
                            return lines[0].strip()
        except Exception as e:
            logger.warning(f"Error querying sink-inputs: {e}")
        return None

    def start_loopback(self, source: Optional[str] = None, sink: Optional[str] = None) -> bool:
        if not self.is_available():
            self.last_error = "pactl not available on system"
            logger.info("[AudioRouter] pactl not available, native loopback skipped")
            return False

        self.cleanup_stale_modules()

        cfg = load_config()
        if not source:
            source = cfg.get("audio_input_device") or "default"
        if not sink:
            sink = cfg.get("audio_output_device") or "default"

        self.current_source = source
        self.current_sink = sink

        # latency_msec=1 forces PipeWire to allocate minimum buffers (typically 16-64 samples, < 1ms latency)
        # channels=1 sums Singer 1 (Left) and Singer 2 (Right) into MONO so both singers are heard on all speakers
        cmd = [
            "pactl", "load-module", "module-loopback",
            "latency_msec=1",
            "channels=1",
            'sink_input_properties="media.name=TikiKaraoke_Mic"'
        ]
        if source and source != "default":
            cmd.append(f"source={source}")
        if sink and sink != "default":
            cmd.append(f"sink={sink}")

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0 and res.stdout.strip().isdigit():
                self.module_id = res.stdout.strip()
                self.is_active = True
                self.last_error = None
                logger.info(f"[AudioRouter] ✅ Loaded low-latency PipeWire loopback (Module #{self.module_id})")
                self.apply_state()
                return True
            else:
                err_msg = res.stderr.strip() or "pactl load-module failed"
                self.last_error = err_msg
                logger.warning(f"[AudioRouter] Failed to load module-loopback: {err_msg}")
                return False
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[AudioRouter] Error loading module-loopback: {e}")
            return False

    def stop_loopback(self):
        if self.module_id and self.is_available():
            try:
                subprocess.run(["pactl", "unload-module", self.module_id], capture_output=True, timeout=1.5)
                logger.info(f"[AudioRouter] Unloaded module-loopback #{self.module_id}")
            except Exception as e:
                logger.warning(f"[AudioRouter] Failed to unload module-loopback #{self.module_id}: {e}")
            self.module_id = None
        self.cleanup_stale_modules()
        self.is_active = False

    def cleanup_stale_modules(self):
        if not self.is_available():
            return
        try:
            res = subprocess.run(["pactl", "list", "modules"], capture_output=True, text=True, timeout=1.5)
            if res.returncode == 0:
                blocks = res.stdout.split("Module #")
                for b in blocks[1:]:
                    if "module-loopback" in b and "TikiKaraoke_Mic" in b:
                        mod_num = b.splitlines()[0].strip()
                        subprocess.run(["pactl", "unload-module", mod_num], capture_output=True, timeout=1.0)
        except Exception as e:
            logger.warning(f"[AudioRouter] Stale module cleanup failed: {e}")

    def set_muted(self, muted: bool):
        self.is_muted = bool(muted)
        if not self.is_available():
            return

        if self.is_muted:
            # Mute the active loopback stream
            sink_in = self.get_loopback_sink_input_id()
            if sink_in:
                subprocess.run(["pactl", "set-sink-input-mute", sink_in, "1"], capture_output=True, timeout=1.0)
            else:
                self.stop_loopback()
        else:
            # Unmute: ensure loopback is running then unmute
            if not self.is_active or not self.module_id:
                self.start_loopback(self.current_source, self.current_sink)
            sink_in = self.get_loopback_sink_input_id()
            if sink_in:
                subprocess.run(["pactl", "set-sink-input-mute", sink_in, "0"], capture_output=True, timeout=1.0)
                # Ensure volume is applied
                subprocess.run(["pactl", "set-sink-input-volume", sink_in, f"{self.volume_pct}%"], capture_output=True, timeout=1.0)

    def set_volume(self, pct: int):
        self.volume_pct = max(0, min(150, int(pct)))
        if not self.is_available():
            return
        sink_in = self.get_loopback_sink_input_id()
        if sink_in:
            subprocess.run(["pactl", "set-sink-input-volume", sink_in, f"{self.volume_pct}%"], capture_output=True, timeout=1.0)

    def set_device(self, device: str):
        if not device:
            return
        if device != self.current_source:
            self.current_source = device
            if self.is_active and not self.is_muted:
                self.start_loopback(self.current_source, self.current_sink)

    def apply_state(self):
        sink_in = self.get_loopback_sink_input_id()
        if sink_in:
            subprocess.run(["pactl", "set-sink-input-mute", sink_in, "1" if self.is_muted else "0"], capture_output=True, timeout=1.0)
            subprocess.run(["pactl", "set-sink-input-volume", sink_in, f"{self.volume_pct}%"], capture_output=True, timeout=1.0)
