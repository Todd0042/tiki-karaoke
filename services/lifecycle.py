"""
System lifecycle and desktop bridge callbacks for Tiki Karaoke.
Allows GUI and desktop components (ControlHub, tray, main process) to register callbacks
that FastAPI route handlers can trigger asynchronously.
"""

from typing import Optional, Callable

shutdown_trigger_callback: Optional[Callable] = None
focus_hub_trigger_callback: Optional[Callable] = None
tunnel_reload_trigger_callback: Optional[Callable] = None
launch_importer_trigger_callback: Optional[Callable] = None
launch_cdg_ripper_trigger_callback: Optional[Callable] = None


def register_shutdown_trigger(cb: Callable):
    global shutdown_trigger_callback
    shutdown_trigger_callback = cb


def register_focus_hub_trigger(cb: Callable):
    global focus_hub_trigger_callback
    focus_hub_trigger_callback = cb


def register_launch_importer_trigger(cb: Callable):
    global launch_importer_trigger_callback
    launch_importer_trigger_callback = cb


def register_launch_cdg_ripper_trigger(cb: Callable):
    global launch_cdg_ripper_trigger_callback
    launch_cdg_ripper_trigger_callback = cb


def register_tunnel_reload_trigger(cb: Callable):
    global tunnel_reload_trigger_callback
    tunnel_reload_trigger_callback = cb


def get_tunnel_reload_trigger() -> Optional[Callable]:
    return tunnel_reload_trigger_callback


def get_shutdown_trigger() -> Optional[Callable]:
    return shutdown_trigger_callback


def get_focus_hub_trigger() -> Optional[Callable]:
    return focus_hub_trigger_callback


def get_launch_importer_trigger() -> Optional[Callable]:
    return launch_importer_trigger_callback


def get_launch_cdg_ripper_trigger() -> Optional[Callable]:
    return launch_cdg_ripper_trigger_callback
