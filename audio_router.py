import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from config import load_config, get_bundled_binary_path

logger = logging.getLogger("tiki.audio_router")

MIC_ROUTER_HELPER = "tiki-mic-router"


class NativeAudioRouter:
    """
    Manages low-latency native hardware microphone loopback via PipeWire / PulseAudio.
    Achieves sub-2ms vocal round-trip latency by bypassing user-space browser WebRTC audio.
    """

    def __init__(self):
        self.module_id: Optional[str] = None
        self.current_source: str = "default"
        self.current_sink: str = "default"
        self.is_muted: bool = True
        self.volume_pct: int = 80
        self.is_active: bool = False

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
                logger.info(f"[AudioRouter] ✅ Loaded low-latency PipeWire loopback (Module #{self.module_id})")
                self.apply_state()
                return True
            else:
                logger.warning(f"[AudioRouter] Failed to load module-loopback: {res.stderr.strip()}")
                return False
        except Exception as e:
            logger.error(f"[AudioRouter] Error loading module-loopback: {e}")
            return False

    def stop_loopback(self):
        if self.module_id and self.is_available():
            try:
                subprocess.run(["pactl", "unload-module", self.module_id], capture_output=True, timeout=1.5)
                logger.info(f"[AudioRouter] Unloaded module-loopback #{self.module_id}")
            except Exception:
                pass
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
        except Exception:
            pass

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

    def get_state(self) -> dict:
        return {
            "available": self.is_available(),
            "active": self.is_active,
            "muted": self.is_muted,
            "volume": self.volume_pct,
            "device": self.current_source
        }


class WasapiAudioRouter:
    """
    Windows native low-latency mic loopback via the bundled tiki-mic-router
    helper (WASAPI shared mode, event-driven). Counterpart of the Linux
    pactl module-loopback path; same public interface, so app.py/main.py
    are platform-agnostic.

    The helper is a child process speaking JSON-lines on stdin/stdout.
    It exits when stdin closes, so a server crash never leaves the mic hot.
    A watchdog restarts the helper if it crashes mid-performance.
    """

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._rx: "queue.Queue" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._watchdog: Optional[threading.Thread] = None
        self._exiting = False

        self.current_source: str = "default"
        self.current_sink: str = "default"
        self.is_muted: bool = True
        self.volume_pct: int = 80
        self.is_active: bool = False
        self.last_negotiated: dict = {}

    # ---------------- helper discovery ----------------

    @staticmethod
    def _helper_path() -> Optional[Path]:
        try:
            p = get_bundled_binary_path(MIC_ROUTER_HELPER)
            if p and Path(p).exists():
                return Path(p)
        except Exception:
            pass
        return None

    def is_available(self) -> bool:
        return sys.platform == "win32" and self._helper_path() is not None

    # ---------------- process + protocol plumbing ----------------

    def _ensure_proc(self) -> bool:
        """Start the helper if not running. Caller holds self._lock."""
        if self.proc is not None and self.proc.poll() is None:
            return True
        helper = self._helper_path()
        if not helper:
            return False
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(
                [str(helper)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as e:
            logger.error(f"[AudioRouter] Failed to launch {MIC_ROUTER_HELPER}: {e}")
            self.proc = None
            return False
        self._rx = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_loop, args=(self.proc.stdout,), daemon=True,
            name="mic-router-reader"
        )
        self._reader.start()
        hello = self._recv(5.0)
        if not hello or hello.get("event") != "hello":
            logger.error(f"[AudioRouter] {MIC_ROUTER_HELPER} did not greet (got {hello})")
            self._kill_proc()
            return False
        logger.info(f"[AudioRouter] {MIC_ROUTER_HELPER} helper started (proto {hello.get('proto')}, v{hello.get('version')})")
        return True

    def _read_loop(self, stream):
        try:
            for line in stream:
                line = line.strip()
                if line:
                    self._rx.put(line)
        except Exception:
            pass
        finally:
            self._rx.put("")  # EOF sentinel

    def _kill_proc(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _recv(self, timeout: float) -> Optional[dict]:
        try:
            line = self._rx.get(timeout=timeout)
        except queue.Empty:
            return None
        if not line:
            return None
        try:
            return json.loads(line)
        except Exception:
            logger.warning(f"[AudioRouter] Non-JSON from helper: {line[:120]}")
            return None

    def _cmd(self, obj: dict, timeout: float = 4.0) -> Optional[dict]:
        """Send a command, return the next event. Restarts helper if dead."""
        with self._lock:
            if self._exiting:
                return None
            if not self._ensure_proc():
                return None
            try:
                self.proc.stdin.write(json.dumps(obj) + "\n")
                self.proc.stdin.flush()
            except Exception as e:
                logger.warning(f"[AudioRouter] Write to helper failed: {e}")
                self._kill_proc()
                return None
            return self._recv(timeout)

    # ---------------- watchdog ----------------

    def _start_watchdog(self):
        with self._lock:
            if self._watchdog and self._watchdog.is_alive():
                return
            self._watchdog = threading.Thread(
                target=self._watchdog_loop, daemon=True, name="mic-router-watchdog"
            )
            self._watchdog.start()

    def _watchdog_loop(self):
        while not self._exiting:
            time.sleep(2.0)
            with self._lock:
                proc = self.proc
                if proc is None or self._exiting:
                    return
                if proc.poll() is None:
                    continue
                # helper crashed
                was_active = self.is_active
                self.proc = None
                self.is_active = False
                self.last_negotiated = {}
            if was_active and not self.is_muted:
                logger.warning(f"[AudioRouter] {MIC_ROUTER_HELPER} crashed mid-performance; restarting loopback...")
                self.start_loopback(self.current_source, self.current_sink)
            else:
                return

    # ---------------- public interface (mirrors NativeAudioRouter) ----------------

    def get_loopback_sink_input_id(self) -> Optional[str]:
        # pactl-specific concept; kept for interface parity
        return None

    def start_loopback(self, source: Optional[str] = None, sink: Optional[str] = None) -> bool:
        if not self.is_available():
            logger.info(f"[AudioRouter] {MIC_ROUTER_HELPER} helper not found; native loopback unavailable on this Windows install")
            return False

        cfg = load_config()
        if not source:
            source = cfg.get("audio_input_device") or "default"
        if not sink:
            sink = cfg.get("audio_output_device") or "default"
        self.current_source = source
        self.current_sink = sink

        resp = self._cmd({
            "cmd": "start",
            "in": self.current_source,
            "out": self.current_sink,
            "gain": max(0.0, min(1.5, self.volume_pct / 100.0)),
            "muted": bool(self.is_muted),
        }, timeout=6.0)

        if resp and resp.get("event") == "started":
            self.is_active = True
            self.last_negotiated = {
                "rate": resp.get("rate"),
                "latency_ms_est": resp.get("latency_ms_est"),
                "out_device": resp.get("out_device"),
            }
            logger.info(
                f"[AudioRouter] ✅ WASAPI loopback active: {resp.get('in_device')} → {resp.get('out_device')} "
                f"(~{resp.get('latency_ms_est', 0):.1f} ms buffering est.)"
            )
            self._start_watchdog()
            return True

        msg = (resp or {}).get("message", f"no response ({resp})")
        logger.warning(f"[AudioRouter] Helper start failed: {msg}")
        self.is_active = False
        return False

    def stop_loopback(self):
        self._cmd({"cmd": "stop"}, timeout=2.0)
        self.is_active = False
        self.last_negotiated = {}

    def cleanup_stale_modules(self):
        # pactl-specific; the helper self-terminates on stdin EOF (no orphans)
        return

    def set_muted(self, muted: bool):
        self.is_muted = bool(muted)
        if not self.is_available():
            return
        if self.is_muted:
            # Stop streaming entirely: releases the mic (privacy LED off,
            # zero CPU). Mirrors Linux behavior of stopping the loopback.
            self._cmd({"cmd": "stop"}, timeout=2.0)
            self.is_active = False
        else:
            if not self.is_active:
                self.start_loopback(self.current_source, self.current_sink)
            self._cmd({"cmd": "set_muted", "muted": False}, timeout=2.0)
            self._cmd({"cmd": "set_gain", "gain": max(0.0, min(1.5, self.volume_pct / 100.0))}, timeout=2.0)

    def set_volume(self, pct: int):
        self.volume_pct = max(0, min(150, int(pct)))
        if not self.is_available():
            return
        self._cmd({"cmd": "set_gain", "gain": max(0.0, min(1.5, self.volume_pct / 100.0))}, timeout=2.0)

    def set_device(self, device: str):
        if not device:
            return
        if device != self.current_source:
            self.current_source = device
            if self.is_active and not self.is_muted:
                self.start_loopback(self.current_source, self.current_sink)

    def apply_state(self):
        if self.is_active:
            self._cmd({"cmd": "set_muted", "muted": bool(self.is_muted)}, timeout=2.0)
            self._cmd({"cmd": "set_gain", "gain": max(0.0, min(1.5, self.volume_pct / 100.0))}, timeout=2.0)

    def get_state(self) -> dict:
        st = {
            "available": self.is_available(),
            "active": self.is_active,
            "muted": self.is_muted,
            "volume": self.volume_pct,
            "device": self.current_source,
        }
        st.update(self.last_negotiated)
        return st

    def shutdown(self):
        """Full teardown (server exit). Helper also dies on stdin EOF."""
        self._exiting = True
        with self._lock:
            try:
                if self.proc and self.proc.poll() is None:
                    self.proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    self.proc.stdin.flush()
            except Exception:
                pass
            self._kill_proc()
        self.is_active = False


# Platform factory: Windows uses the WASAPI helper; everything else keeps
# the pactl/PipeWire path. Same public interface either way.
if sys.platform == "win32":
    audio_router = WasapiAudioRouter()
else:
    audio_router = NativeAudioRouter()
