import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

test_dir = tempfile.mkdtemp(prefix="tiki_proxy_room_test_")
os.environ["TIKI_DATA_DIR"] = test_dir
os.environ["TIKI_TESTING"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app import app
from config import HOST_TOKEN, save_config
from database import init_db, set_setting
from services.auth import (
    failed_room_attempts,
    get_active_room_code,
    get_client_ip,
    is_local_request,
    is_proxied_request,
)


def _fake_request(host: str, headers=None):
    return SimpleNamespace(
        client=SimpleNamespace(host=host) if host else None,
        headers={k.lower(): v for k, v in (headers or {}).items()},
        cookies={},
        query_params={},
    )


class TestProxyAndRoomCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        save_config({"configured": True, "host_pin": "9876"})

    def setUp(self):
        set_setting("require_room_code", "false")
        failed_room_attempts.clear()

    def test_proxy_detection_and_real_ip(self):
        direct_local = _fake_request("127.0.0.1")
        self.assertFalse(is_proxied_request(direct_local))
        self.assertTrue(is_local_request(direct_local))
        self.assertEqual(get_client_ip(direct_local), "127.0.0.1")

        proxied_cf = _fake_request(
            "127.0.0.1",
            headers={"CF-Connecting-IP": "198.51.100.42", "X-Forwarded-For": "198.51.100.42"},
        )
        self.assertTrue(is_proxied_request(proxied_cf))
        self.assertFalse(is_local_request(proxied_cf))
        self.assertEqual(get_client_ip(proxied_cf), "198.51.100.42")

        spoofed_remote = _fake_request("203.0.113.9", headers={"X-Forwarded-For": "127.0.0.1"})
        self.assertFalse(is_local_request(spoofed_remote))
        self.assertEqual(get_client_ip(spoofed_remote), "203.0.113.9")

    def test_stage_blocked_when_proxied_and_unauthenticated(self):
        with TestClient(app) as client:
            res = client.get("/stage", headers={"CF-Connecting-IP": "198.51.100.42"})
            self.assertEqual(res.status_code, 403)

            host_res = client.get(
                f"/stage?token={HOST_TOKEN}",
                headers={"CF-Connecting-IP": "198.51.100.42", "X-Forwarded-Proto": "https"},
            )
            self.assertEqual(host_res.status_code, 200)

    def test_host_pin_sets_httponly_and_secure_cookie_on_https(self):
        with TestClient(app) as client:
            res = client.post(
                "/api/auth/pin",
                json={"pin": "9876"},
                headers={"CF-Connecting-IP": "198.51.100.77", "X-Forwarded-Proto": "https"},
            )
            self.assertEqual(res.status_code, 200)
            set_cookie = res.headers.get("set-cookie", "").lower()
            self.assertIn("tiki_host=", set_cookie)
            self.assertIn("httponly", set_cookie)
            self.assertIn("secure", set_cookie)

    def test_room_code_gating_verify_and_rotate(self):
        set_setting("require_room_code", "true")
        code = get_active_room_code()
        self.assertEqual(len(code), 4)

        with TestClient(app) as client:
            # Unverified guest blocked on search, library, register, and /qr/public
            self.assertEqual(client.get("/api/library").status_code, 403)
            self.assertEqual(client.get("/api/search?q=test").status_code, 403)
            self.assertEqual(
                client.post("/api/singer/register", json={"token": "tok1", "name": "Alice", "avatar": "🌺"}).status_code,
                403,
            )
            self.assertEqual(client.get("/qr/public").status_code, 403)

            # Wrong code rejected
            bad = client.post("/api/room/verify", json={"code": "0000"})
            self.assertEqual(bad.status_code, 401)

            # Valid code sets tiki_room cookie and unlocks library
            ok = client.post("/api/room/verify", json={"code": code.lower()})
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.json().get("verified"))
            self.assertEqual(client.get("/api/library").status_code, 200)

            # Rotate code invalidates old code
            rot = client.post("/api/host/room-code/rotate", headers={"X-Host-Token": HOST_TOKEN})
            self.assertEqual(rot.status_code, 200)
            new_code = rot.json()["room_code"]
            self.assertNotEqual(new_code, code)

            # Old cookie is now rejected
            self.assertEqual(client.get("/api/library").status_code, 403)

            # Scanning /join?room=NEW_CODE unlocks via cookie
            join_res = client.get(f"/join?room={new_code}")
            self.assertEqual(join_res.status_code, 200)
            self.assertEqual(client.get("/api/library").status_code, 200)


if __name__ == "__main__":
    unittest.main()
