"""
WebSocket Connection Manager for Tiki Karaoke Lounge.
Manages active WebSocket connections across host DJ, stage displays, and mobile singers.
"""

import time
from typing import List, Dict, Any, Optional
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.stage_displays: Dict[str, Dict[str, Any]] = {}
        self.ws_to_display_id: Dict[WebSocket, str] = {}
        self.host_connections: set = set()
        self.emoji_times: Dict[WebSocket, List[float]] = {}

    async def connect(self, websocket: WebSocket, is_host: bool = False):
        await websocket.accept()
        self.active_connections.append(websocket)
        if is_host:
            self.host_connections.add(websocket)
        self.emoji_times[websocket] = []

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.host_connections.discard(websocket)
        self.emoji_times.pop(websocket, None)
        disp_id = self.ws_to_display_id.pop(websocket, None)
        if disp_id and disp_id in self.stage_displays:
            del self.stage_displays[disp_id]

    def register_stage_display(self, websocket: WebSocket, display_id: str, name: str, is_muted: bool, ip: str = ""):
        self.stage_displays[display_id] = {
            "display_id": display_id,
            "name": name,
            "is_muted": is_muted,
            "ip": ip,
            "last_seen": time.time()
        }
        self.ws_to_display_id[websocket] = display_id

    def is_display_authorized(self, websocket: WebSocket, display_id: str) -> bool:
        if websocket in self.host_connections:
            return True
        return self.ws_to_display_id.get(websocket) == display_id

    def check_emoji_rate_limit(self, websocket: WebSocket) -> bool:
        now = time.time()
        times = self.emoji_times.get(websocket, [])
        times = [t for t in times if now - t < 1.0]
        if len(times) >= 5:
            self.emoji_times[websocket] = times
            return False
        times.append(now)
        self.emoji_times[websocket] = times
        return True

    def set_display_muted(self, display_id: str, is_muted: bool):
        if display_id in self.stage_displays:
            self.stage_displays[display_id]["is_muted"] = is_muted

    def get_stage_displays(self) -> List[Dict[str, Any]]:
        return list(self.stage_displays.values())

    async def broadcast(self, message: Dict[str, Any]):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


# Global singleton instance
manager = ConnectionManager()
