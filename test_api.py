import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

# Create isolated test environment before importing app/config
test_dir = tempfile.mkdtemp(prefix="tiki_api_test_")
os.environ["TIKI_DATA_DIR"] = test_dir
os.environ["TIKI_TESTING"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import webbrowser
webbrowser.open = lambda *args, **kwargs: True

from fastapi.testclient import TestClient
from app import app
from database import init_db

class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TIKI_TESTING"] = "1"
        init_db()
        from database import get_setting, set_setting
        cls.default_strict_mode = get_setting("strict_byom_only")
        set_setting("strict_byom_only", "false")
        from config import save_config
        save_config({"configured": True})
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        # Release any native mic loopback loaded by host_mic_settings tests
        # (Linux: pactl module-loopback; Windows: helper stream) so the test
        # suite never leaves the microphone held open.
        try:
            from audio_router import audio_router
            audio_router.stop_loopback()
            audio_router.cleanup_stale_modules()
        except Exception:
            pass
        # Kill any browser or helper subprocesses that may have been spawned
        try:
            import main
            for proc in getattr(main, "launched_browser_procs", []):
                try:
                    if proc and proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=1.0)
                except Exception:
                    pass
        except Exception:
            pass
        shutil.rmtree(test_dir, ignore_errors=True)

    def setUp(self):
        self.client.cookies.clear()
        from app import (
            failed_pin_attempts, search_requests_by_ip,
            last_queue_time_by_singer
        )
        failed_pin_attempts.clear()
        search_requests_by_ip.clear()
        last_queue_time_by_singer.clear()
        from database import get_db
        conn = get_db()
        with conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DELETE FROM queue")
            conn.execute("DELETE FROM history")
            conn.execute("DELETE FROM singer_song_log")
            conn.execute("DELETE FROM filler_tracks")
            conn.execute("DELETE FROM tracks")
            conn.execute("PRAGMA foreign_keys = ON")
        conn.close()

    def test_pages(self):
        # Home/Join page
        r = self.client.get("/join")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Tiki", r.text)

        # Stage page
        r = self.client.get("/stage")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Main Stage", r.text)

    def test_qr_codes(self):
        from config import HOST_TOKEN
        # Public QR is open
        r = self.client.get("/qr/public")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")

        # Host QR requires host authorization
        r = self.client.get("/qr/host")
        self.assertEqual(r.status_code, 403)

        # Authorized via header
        r = self.client.get("/qr/host", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")

        # Authorized via cookie
        r = self.client.get("/qr/host", cookies={"tiki_host": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")

    def test_singer_and_queue_flow(self):
        from config import HOST_TOKEN
        # 1. Register singer
        r = self.client.post("/api/singer/register", json={
            "token": "test_singer_token",
            "name": "Alex",
            "avatar": "🦜"
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["name"], "Alex")

        # 2. Queue a song
        r = self.client.post("/api/queue", json={
            "video_id": "test_vid_api",
            "title": "Sweet Caroline",
            "artist": "Neil Diamond",
            "singer_token": "test_singer_token",
            "duet_partner": "Sarah",
            "pitch_shift": -1
        })
        self.assertEqual(r.status_code, 200)
        q_data = r.json()
        self.assertIn("queue_id", q_data)

        # 3. Check queue
        r = self.client.get("/api/queue")
        self.assertEqual(r.status_code, 200)
        queue = r.json()
        self.assertGreaterEqual(len(queue), 1)
        found = next((q for q in queue if q["queue_id"] == q_data["queue_id"]), None)
        self.assertIsNotNone(found)
        self.assertEqual(found["singer_name"], "Alex")
        self.assertEqual(found["pitch_shift"], -1)

        # 4. Check state
        r = self.client.get("/api/state")
        self.assertEqual(r.status_code, 200)
        state = r.json()
        self.assertIn("queue", state)

        # 5. Play item (unauthorized returns 403, authorized returns 200)
        r = self.client.post(f"/api/queue/{q_data['queue_id']}/play")
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/api/queue/{q_data['queue_id']}/play", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)

        # 6. Complete item (unauthorized returns 403, authorized returns 200)
        r = self.client.post(f"/api/queue/{q_data['queue_id']}/complete")
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/api/queue/{q_data['queue_id']}/complete", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)

        # 7. Check singer history
        r = self.client.get("/api/singer/test_singer_token/history")
        self.assertEqual(r.status_code, 200)
        history = r.json()
        self.assertGreaterEqual(len(history), 1)

    def test_settings_api(self):
        from config import HOST_TOKEN
        # Unauthorized update fails
        r = self.client.post("/api/settings", json={"tts_enabled": "true"})
        self.assertEqual(r.status_code, 403)

        # Authorized update succeeds
        r = self.client.post("/api/settings", json={"tts_enabled": "true"}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tts_enabled"], "true")

    def test_storage_inspect_and_setup(self):
        from config import HOST_TOKEN
        # 1. Inspect without host token returns 403 when configured
        r = self.client.post("/api/setup/inspect-path", json={"path": "."})
        self.assertEqual(r.status_code, 403)

        # Authorized inspect succeeds
        r = self.client.post("/api/setup/inspect-path", json={"path": "."}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("valid"))
        self.assertGreater(data.get("free_gb", 0), 0)

        # 2. Setup page renders with host credentials
        r = self.client.get("/setup", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Storage & Media Library", r.text)

        # 3. Test Display Identification endpoints
        r = self.client.post("/api/setup/test-display", json={
            "display_id": 0,
            "name": "Test Monitor 0",
            "role": "stage",
            "x": 0,
            "y": 0,
            "width": 1920,
            "height": 1080
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        disp_data = r.json()
        self.assertEqual(disp_data.get("status"), "ok")
        self.assertIn("url", disp_data)

        # Test Identify page HTML content and closing script
        r = self.client.get("/stage/identify?id=0&name=Test+Monitor&role=stage")
        self.assertEqual(r.status_code, 200)
        self.assertIn("STAGE DISPLAY #0", r.text)
        self.assertIn("window.close()", r.text)

        # 4. Test Directory Browsing and Drive Switching
        r = self.client.get("/api/setup/browse-directories", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        browse_data = r.json()
        self.assertEqual(browse_data.get("status"), "ok")
        self.assertIn("current", browse_data)
        self.assertIn("directories", browse_data)

        # Test Native Directory Picker endpoint
        r = self.client.post("/api/setup/pick-directory", json={"initial_path": "."}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertIn(r.json().get("status"), ["ok", "fallback", "cancelled"])

        # 5. Test Audio Output Test endpoint
        r = self.client.post("/api/setup/test-audio", json={"device": "default"}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "ok")

    def test_library_browsing_and_storage_management(self):
        from config import HOST_TOKEN
        # 1. Test Library browsing endpoint
        r = self.client.get("/api/library?sort=popularity")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("tracks", data)
        self.assertIn("artists", data)
        self.assertIn("total", data)
        self.assertIn("page", data)

        # 2. Test Bring It Back sort
        r = self.client.get("/api/library?sort=bring_back")
        self.assertEqual(r.status_code, 200)
        self.assertIn("tracks", r.json())

        # 3. Test Storage status
        r = self.client.get("/api/storage/status")
        self.assertEqual(r.status_code, 200)
        status = r.json()
        self.assertIn("free_gb", status)
        self.assertIn("used_percent", status)
        self.assertIn(status["status_color"], ["green", "orange", "red"])

        # 4. Test Cleanup candidates
        r = self.client.get("/api/storage/cleanup-candidates?sort_by=oldest_played")
        self.assertEqual(r.status_code, 200)
        clean = r.json()
        self.assertIn("candidates", clean)

        # 5. Test Storage prune (Unauthorized without host token)
        r = self.client.post("/api/storage/prune", json={"track_ids": [999999]})
        self.assertEqual(r.status_code, 403)

        # 6. Test Storage prune with host header
        r = self.client.post("/api/storage/prune", json={"track_ids": [999999]}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_display_and_audio_setup(self):
        from config import HOST_TOKEN
        # 1. Test displays discovery API (requires host)
        r = self.client.get("/api/setup/displays")
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/api/setup/displays", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        displays = r.json()
        self.assertIsInstance(displays, list)
        self.assertGreaterEqual(len(displays), 1)
        self.assertIn("id", displays[0])
        self.assertIn("name", displays[0])
        self.assertIn("width", displays[0])
        self.assertIn("height", displays[0])

        # 2. Test audio sinks discovery API (requires host)
        r = self.client.get("/api/setup/audio-devices")
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/api/setup/audio-devices", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        sinks = r.json()
        self.assertIsInstance(sinks, list)
        self.assertGreaterEqual(len(sinks), 1)
        self.assertIn("id", sinks[0])
        self.assertIn("description", sinks[0])

        # 3. Test test-display broadcast API (requires host)
        r = self.client.post("/api/setup/test-display", json={"badge": "📺 MAIN STAGE DISPLAY"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/api/setup/test-display", json={"badge": "📺 MAIN STAGE DISPLAY"}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "ok")

        # 4. Test WebSocket stage registration & state gating
        with self.client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "stage_register",
                "display_id": "test_stage_screen_1",
                "name": "Test Stage Monitor",
                "is_muted": False
            })
            resp = ws.receive_json()
            self.assertEqual(resp.get("type"), "displays_updated")
            displays_active = resp.get("displays", [])
            self.assertTrue(any(d["display_id"] == "test_stage_screen_1" for d in displays_active))

            # State check: has_stage_display should now be True
            r_state = self.client.get("/api/state")
            self.assertEqual(r_state.status_code, 200)
            self.assertTrue(r_state.json()["has_stage_display"])

    def test_duplicate_song_queue_and_duet_api(self):
        # 1. Register 2 singers
        r = self.client.post("/api/singer/register", json={"token": "tok_singer_a", "name": "SingerA", "avatar": "🌴"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/singer/register", json={"token": "tok_singer_b", "name": "SingerB", "avatar": "🦜"})
        self.assertEqual(r.status_code, 200)

        # 2. Singer A queues "Hotel California"
        r = self.client.post("/api/queue", json={
            "video_id": "v_hotel_cal",
            "title": "Hotel California",
            "artist": "Eagles",
            "singer_token": "tok_singer_a"
        })
        self.assertEqual(r.status_code, 200)
        q_id = r.json()["queue_id"]

        # 3. Check duplicate endpoint
        r = self.client.get("/api/queue/check-duplicate?singer_token=tok_singer_b&title=Hotel%20California&artist=Eagles")
        self.assertEqual(r.status_code, 200)
        dup_data = r.json().get("duplicate")
        self.assertIsNotNone(dup_data)
        self.assertEqual(dup_data["existing_singer_name"], "SingerA")

        # 4. Singer B attempts to queue "Hotel California" without force -> 409 duplicate warning
        r = self.client.post("/api/queue", json={
            "video_id": "v_hotel_cal",
            "title": "Hotel California",
            "artist": "Eagles",
            "singer_token": "tok_singer_b",
            "force": False
        })
        self.assertEqual(r.status_code, 409)
        resp_dup = r.json()
        self.assertEqual(resp_dup.get("status"), "duplicate_warning")
        self.assertEqual(resp_dup["duplicate"]["existing_singer_name"], "SingerA")
        self.assertEqual(resp_dup["duplicate"]["match_type"], "other_singer")

        # 5. Singer B joins as duet partner to Singer A's queue item
        r = self.client.post(f"/api/queue/{q_id}/join-duet", json={
            "singer_token": "tok_singer_b",
            "partner_name": "SingerB"
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["duet_partner"], "SingerB")

        # 5b. Singer B leaves duet -> partner cleared
        r = self.client.post(f"/api/queue/{q_id}/leave-duet", json={
            "singer_token": "tok_singer_b",
            "singer_name": "SingerB"
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result"]["duet_partner"], "")

        # 5c. Singer B rejoins duet
        r = self.client.post(f"/api/queue/{q_id}/join-duet", json={
            "singer_token": "tok_singer_b",
            "partner_name": "SingerB"
        })
        self.assertEqual(r.status_code, 200)

        # 5d. Singer A (primary) leaves duet -> Singer B promoted to primary!
        r = self.client.post(f"/api/queue/{q_id}/leave-duet", json={
            "singer_token": "tok_singer_a",
            "singer_name": "SingerA"
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result"]["primary_name"], "SingerB")
        self.assertEqual(r.json()["result"]["duet_partner"], "")

        # 6. Singer B queues with force: True -> succeeds
        r = self.client.post("/api/queue", json={
            "video_id": "v_hotel_cal",
            "title": "Hotel California",
            "artist": "Eagles",
            "singer_token": "tok_singer_b",
            "force": True
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("queue_id", r.json())

    def test_guest_fallback_creation(self):
        # Multiple anonymous queue requests with distinct new tokens
        for i in range(5):
            r = self.client.post("/api/queue", json={
                "video_id": f"v_guest_{i}",
                "title": f"Guest Song {i}",
                "artist": "Guest Artist",
                "singer_token": f"anon_token_{i}"
            })
            self.assertEqual(r.status_code, 200)
    def test_bgm_tracks_endpoint(self):
        r = self.client.get("/api/bgm-tracks")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("tracks", data)
        self.assertGreaterEqual(len(data["tracks"]), 5)
        for track in data["tracks"]:
            self.assertTrue(track.endswith(".mp3"))

    def test_host_stop_and_skip_endpoints(self):
        from config import HOST_TOKEN
        # Queue a song
        r = self.client.post("/api/queue", json={
            "video_id": "v_stop_test",
            "title": "Stop Test Song",
            "artist": "Stop Artist",
            "singer_token": "tok_stop_host"
        })
        self.assertEqual(r.status_code, 200)
        q_id = r.json()["queue_id"]

        # Play it with host token
        r = self.client.post(f"/api/queue/{q_id}/play", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)

        # Stop stage without host token returns 403
        r = self.client.post("/api/host/stop")
        self.assertEqual(r.status_code, 403)

        # Stop stage with host token succeeds
        r = self.client.post("/api/host/stop", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

        # Verify state: playing is None, next is the pending song
        r = self.client.get("/api/state")
        self.assertEqual(r.status_code, 200)
        state = r.json()
        self.assertIsNone(state["playing"])
        self.assertIsNotNone(state["next"])
        self.assertEqual(state["next"]["queue_id"], q_id)

        # Skip track without host token returns 403
        r = self.client.post("/api/host/skip")
        self.assertEqual(r.status_code, 403)

        # Skip track with host token succeeds
        r = self.client.post("/api/host/skip", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

        # Verify queue is now empty of pending tracks
        r = self.client.get("/api/state")
        state = r.json()
        self.assertIsNone(state["playing"])
        self.assertIsNone(state["next"])

        # Verify skipping when queue is empty: advances through filler crate
        from database import add_filler_track, update_filler_track_status
        f1 = add_filler_track("yt_f1", "Filler Alpha", "Artist Alpha", "Artist Alpha - Filler Alpha", is_downloaded=1)
        f2 = add_filler_track("yt_f2", "Filler Beta", "Artist Beta", "Artist Beta - Filler Beta", is_downloaded=1)
        update_filler_track_status(f1, "ready", is_downloaded=1)
        update_filler_track_status(f2, "ready", is_downloaded=1)

        r_skip1 = self.client.post("/api/host/skip", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_skip1.status_code, 200)
        self.assertIn("skipped_to_filler", r_skip1.json())
        first_filler = r_skip1.json()["skipped_to_filler"]

        # Skip again: rotates to a different filler track
        r_skip2 = self.client.post("/api/host/skip", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_skip2.status_code, 200)
        self.assertIn("skipped_to_filler", r_skip2.json())
        second_filler = r_skip2.json()["skipped_to_filler"]
        self.assertNotEqual(first_filler, second_filler)

    def test_singer_songbook_and_profile(self):
        token = "tok_scott_songbook"
        # Register singer Scott
        r = self.client.post("/api/singer/register", json={
            "token": token,
            "name": "Scott",
            "avatar": "🌴"
        })
        self.assertEqual(r.status_code, 200)

        # Profile check
        r = self.client.get(f"/api/singer/{token}/profile")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "Scott")
        self.assertEqual(r.json()["avatar"], "🌴")

        from config import HOST_TOKEN
        # Queue Song A twice with pitch +2
        self.client.post("/api/queue", json={
            "video_id": "v_song_a",
            "title": "Song A",
            "artist": "Artist Alpha",
            "singer_token": token,
            "pitch_shift": 2,
            "force": True
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.client.post("/api/queue", json={
            "video_id": "v_song_a",
            "title": "Song A",
            "artist": "Artist Alpha",
            "singer_token": token,
            "pitch_shift": 2,
            "force": True
        }, headers={"X-Host-Token": HOST_TOKEN})

        # Queue Song B once with pitch -1
        self.client.post("/api/queue", json={
            "video_id": "v_song_b",
            "title": "Song B",
            "artist": "Artist Beta",
            "singer_token": token,
            "pitch_shift": -1,
            "force": True
        }, headers={"X-Host-Token": HOST_TOKEN})

        # Fetch Songbook
        r = self.client.get(f"/api/singer/{token}/songbook")
        self.assertEqual(r.status_code, 200)
        book = r.json()
        self.assertEqual(book["singer"]["name"], "Scott")
        self.assertEqual(book["total_queued"], 3)
        self.assertEqual(book["unique_songs"], 2)

        songs = book["songs"]
        self.assertEqual(len(songs), 2)
        # Song A must be first because it was queued 2 times
        self.assertEqual(songs[0]["title"], "Song A")
        self.assertEqual(songs[0]["times_queued"], 2)
        self.assertEqual(songs[0]["preferred_pitch"], 2)

        # Song B must be second (queued 1 time)
        self.assertEqual(songs[1]["title"], "Song B")
        self.assertEqual(songs[1]["times_queued"], 1)
        self.assertEqual(songs[1]["preferred_pitch"], -1)

    def test_host_pin_auth(self):
        from config import HOST_PIN, HOST_TOKEN
        from app import failed_pin_attempts
        failed_pin_attempts.clear()

        # 1. Incorrect PIN returns 401
        r = self.client.post("/api/auth/pin", json={"pin": "9999"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("Invalid Host PIN", r.json().get("detail", ""))

        # 2. Rate limiting after 5 failed attempts returns 429
        for _ in range(4):
            self.client.post("/api/auth/pin", json={"pin": "0000"})
        r = self.client.post("/api/auth/pin", json={"pin": "0000"})
        self.assertEqual(r.status_code, 429)
        self.assertIn("Too many failed PIN attempts", r.json().get("detail", ""))

        # Clear failed attempts to test success
        failed_pin_attempts.clear()

        # 3. Correct PIN returns 200, is_host: True, and sets tiki_host cookie
        r = self.client.post("/api/auth/pin", json={"pin": str(HOST_PIN)})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("is_host"))
        self.assertIn("tiki_host", r.cookies)
        self.assertEqual(r.cookies["tiki_host"], HOST_TOKEN)

    def test_websocket_security_and_throttling(self):
        from config import HOST_TOKEN

        # 1. Unauthenticated WebSocket cannot trigger soundboard
        with self.client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "soundboard_play", "sound": "airhorn"})
            # Send an emoji - first 5 are allowed
            for _ in range(5):
                ws.send_json({"type": "emoji_reaction", "emoji": "🌺"})
                msg = ws.receive_json()
                self.assertEqual(msg.get("type"), "emoji_reaction")

        # 2. Host-authenticated WebSocket can trigger soundboard and broadcast host_mic_settings
        with self.client.websocket_connect(f"/ws?token={HOST_TOKEN}") as ws_host:
            ws_host.send_json({"type": "soundboard_play", "sound": "airhorn"})
            msg = ws_host.receive_json()
            self.assertEqual(msg.get("type"), "soundboard_play")
            self.assertEqual(msg.get("sound"), "airhorn")

            # Test host_mic_settings broadcast
            ws_host.send_json({
                "type": "host_mic_settings",
                "volume": 95,
                "muted": False,
                "reverb": True,
                "ducking_enabled": True,
                "ducking_db": -6
            })
            mic_msg = ws_host.receive_json()
            self.assertEqual(mic_msg.get("type"), "host_mic_settings")
            self.assertEqual(mic_msg.get("volume"), 95)
            self.assertEqual(mic_msg.get("muted"), False)
            self.assertEqual(mic_msg.get("reverb"), True)
            self.assertEqual(mic_msg.get("ducking_enabled"), True)
            self.assertEqual(mic_msg.get("ducking_db"), -6)

    def test_abuse_prevention_quotas_and_cooldown(self):
        from config import HOST_TOKEN
        from app import last_queue_time_by_singer, search_requests_by_ip
        from database import save_track

        singer_token = "tok_quota_test"
        self.client.post("/api/singer/register", json={
            "token": singer_token,
            "name": "QuotaSinger",
            "avatar": "🎤"
        })

        # 1. First queue succeeds
        r1 = self.client.post("/api/queue", json={
            "video_id": "v_quota_1",
            "title": "Song 1",
            "artist": "Artist 1",
            "singer_token": singer_token
        })
        self.assertEqual(r1.status_code, 200)

        # 2. Second queue within 5 seconds triggers 429 cooldown
        r2 = self.client.post("/api/queue", json={
            "video_id": "v_quota_2",
            "title": "Song 2",
            "artist": "Artist 2",
            "singer_token": singer_token
        })
        self.assertEqual(r2.status_code, 429)
        self.assertIn("Please wait 5 seconds", r2.json().get("detail", ""))

        # 3. Queue songs 2, 3, 4, 5 by resetting cooldown timer between calls
        for idx in [2, 3, 4, 5]:
            last_queue_time_by_singer[singer_token] = 0
            r = self.client.post("/api/queue", json={
                "video_id": f"v_quota_{idx}",
                "title": f"Song {idx}",
                "artist": "Artist",
                "singer_token": singer_token
            })
            self.assertEqual(r.status_code, 200)

        # 4. 6th pending song triggers 400 quota reached
        last_queue_time_by_singer[singer_token] = 0
        r6 = self.client.post("/api/queue", json={
            "video_id": "v_quota_6",
            "title": "Song 6",
            "artist": "Artist",
            "singer_token": singer_token
        })
        self.assertEqual(r6.status_code, 400)
        self.assertIn("already have 5 songs", r6.json().get("detail", ""))

        # 5. Host DJ can bypass quota and cooldown
        r_host = self.client.post("/api/queue", json={
            "video_id": "v_quota_host",
            "title": "Host Song",
            "artist": "Artist",
            "singer_token": singer_token
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_host.status_code, 200)

        # 6. 10-minute video duration cap
        long_track_id = save_track(
            video_id="v_long_track_test",
            title="Super Long Symphony",
            artist="Symphony Orchestra",
            original_title="Super Long Symphony",
            duration=601
        )
        r_long = self.client.post("/api/queue", json={
            "track_id": long_track_id,
            "video_id": "v_long_track_test",
            "title": "Super Long Symphony",
            "artist": "Symphony Orchestra",
            "singer_token": "tok_other_singer"
        })
        self.assertEqual(r_long.status_code, 400)
        self.assertIn("over 10 minutes long", r_long.json().get("detail", ""))

        # 7. Search rate limit (20/min per IP)
        search_requests_by_ip.clear()
        for _ in range(20):
            r = self.client.get("/api/search?q=queen")
            self.assertEqual(r.status_code, 200)

        # 21st search triggers 429
        r21 = self.client.get("/api/search?q=queen")
        self.assertEqual(r21.status_code, 429)
        self.assertIn("Search rate limit reached", r21.json().get("detail", ""))

        # Host bypasses search rate limit
        r_host_search = self.client.get("/api/search?q=queen", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_host_search.status_code, 200)

    def test_bgm_filler_and_vibe_engine(self):
        """Test BGM filler track staging, settings, and vibe matching engine."""
        from database import add_filler_track, get_next_vibe_filler_track, update_filler_track_status
        from metadata import canonicalize_genre
        from config import HOST_TOKEN

        # 1. Test canonicalize_genre
        self.assertEqual(canonicalize_genre("Classic Rock"), "Rock")
        self.assertEqual(canonicalize_genre("Dance Pop"), "Dance")
        self.assertEqual(canonicalize_genre("Country Road"), "Country")
        self.assertEqual(canonicalize_genre("hip hop"), "Hip-Hop")

        # 2. Test Staging Filler Track via API (Requires Host Auth)
        r_unauth = self.client.post("/api/host/filler-tracks/stage", json={
            "video_id": "vid_rock_1",
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "genre": "Rock",
            "release_year": 1975
        })
        self.assertEqual(r_unauth.status_code, 403)

        r_stage = self.client.post("/api/host/filler-tracks/stage", json={
            "video_id": "vid_rock_1",
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "genre": "Rock",
            "release_year": 1975
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_stage.status_code, 200)

        # Stage second track (same artist)
        r_stage2 = self.client.post("/api/host/filler-tracks/stage", json={
            "video_id": "vid_rock_2",
            "title": "We Will Rock You",
            "artist": "Queen",
            "genre": "Rock",
            "release_year": 1977
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_stage2.status_code, 200)

        # Stage third track (different artist, same genre)
        r_stage3 = self.client.post("/api/host/filler-tracks/stage", json={
            "video_id": "vid_rock_3",
            "title": "Sweet Child O Mine",
            "artist": "Guns N Roses",
            "genre": "Rock",
            "release_year": 1987
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_stage3.status_code, 200)

        # Stage fourth track (different genre)
        r_stage4 = self.client.post("/api/host/filler-tracks/stage", json={
            "video_id": "vid_pop_1",
            "title": "Levitating",
            "artist": "Dua Lipa",
            "genre": "Pop",
            "release_year": 2020
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_stage4.status_code, 200)

        # Verify tracks listed
        r_list = self.client.get("/api/host/filler-tracks", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_list.status_code, 200)
        tracks = r_list.json()
        self.assertGreaterEqual(len(tracks), 4)

        # Mark them ready for vibe engine test
        for t in tracks:
            update_filler_track_status(t["id"], "ready", is_downloaded=1)

        # 3. Test Vibe Matching Engine:
        # If last played was Queen, vibe engine MUST avoid Queen and pick another Rock track (Guns N Roses)!
        vibe_pick = get_next_vibe_filler_track(last_track_artist="Queen", last_track_genre="Rock")
        self.assertIsNotNone(vibe_pick)
        self.assertNotEqual(vibe_pick["artist"].lower(), "queen")
        self.assertEqual(vibe_pick["artist"], "Guns N Roses")

        # If last played was Pop, pick Pop track
        vibe_pop = get_next_vibe_filler_track(last_track_artist="Taylor Swift", last_track_genre="Pop")
        self.assertIsNotNone(vibe_pop)
        self.assertEqual(vibe_pop["artist"], "Dua Lipa")

        # 4. Test Filler Settings API
        r_settings = self.client.get("/api/host/filler-settings", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_settings.status_code, 200)
        self.assertIn("bgm_transition_policy", r_settings.json())

        # Update transition policy to 'finish'
        r_update = self.client.post("/api/host/filler-settings", json={
            "bgm_transition_policy": "finish"
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_update.status_code, 200)

        r_check = self.client.get("/api/host/filler-settings", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_check.json()["bgm_transition_policy"], "finish")

        # 5. Test State API includes filler info
        r_state = self.client.get("/api/state")
        self.assertEqual(r_state.status_code, 200)
        self.assertIn("filler", r_state.json())
        self.assertEqual(r_state.json()["filler"]["transition_policy"], "finish")

    def test_queue_deletion_security(self):
        from config import HOST_TOKEN
        # 1. Register Singer 1 and Singer 2
        r1 = self.client.post("/api/singer/register", json={"name": "Singer1", "token": "tok_del_1"})
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post("/api/singer/register", json={"name": "Singer2", "token": "tok_del_2"})
        self.assertEqual(r2.status_code, 200)

        # 2. Singer 1 queues a track
        r_q = self.client.post("/api/queue", json={
            "video_id": "del_test_song",
            "title": "Song for Deletion",
            "artist": "Del Artist",
            "singer_name": "Singer1",
            "singer_token": "tok_del_1",
            "pitch_shift": 0
        })
        self.assertEqual(r_q.status_code, 200)
        queue_id = r_q.json()["queue_id"]

        # 3. Unauthenticated request without token must fail with 401
        r_unauth = self.client.delete(f"/api/queue/{queue_id}")
        self.assertEqual(r_unauth.status_code, 401)

        # 4. Another singer cannot delete Singer 1's track (returns {"deleted": False})
        r_wrong_singer = self.client.delete(f"/api/queue/{queue_id}?token=tok_del_2")
        self.assertEqual(r_wrong_singer.status_code, 200)
        self.assertFalse(r_wrong_singer.json()["deleted"])

        # Verify track is still in queue
        q_list = self.client.get("/api/queue").json()
        self.assertTrue(any(q["queue_id"] == queue_id for q in q_list))

        # 5. Singer 1 can delete their own track
        r_own = self.client.delete(f"/api/queue/{queue_id}?token=tok_del_1")
        self.assertEqual(r_own.status_code, 200)
        self.assertTrue(r_own.json()["deleted"])

        # Verify track is gone
        q_list2 = self.client.get("/api/queue").json()
        self.assertFalse(any(q["queue_id"] == queue_id for q in q_list2))

        # 6. Host can delete any track
        from app import last_queue_time_by_singer
        last_queue_time_by_singer.clear()
        r_q2 = self.client.post("/api/queue", json={
            "video_id": "del_test_song_2",
            "title": "Song 2 for Host Deletion",
            "artist": "Del Artist 2",
            "singer_name": "Singer1",
            "singer_token": "tok_del_1",
            "pitch_shift": 0
        })
        self.assertEqual(r_q2.status_code, 200)
        queue_id_2 = r_q2.json()["queue_id"]

        r_host_del = self.client.delete(f"/api/queue/{queue_id_2}", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_host_del.status_code, 200)
        self.assertTrue(r_host_del.json()["deleted"])

    def test_host_shutdown_endpoint(self):
        from config import HOST_TOKEN
        from app import register_shutdown_trigger
        shutdown_called = []
        register_shutdown_trigger(lambda: shutdown_called.append(True))

        # 1. Reject unauthorized request
        r = self.client.post("/api/host/shutdown")
        self.assertEqual(r.status_code, 403)

        # 2. Accept host authorized request
        r = self.client.post("/api/host/shutdown", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

        # Wait briefly for delayed exit callback
        import time
        time.sleep(0.8)
        self.assertTrue(len(shutdown_called) > 0)

    def test_sing_along_detection_and_filtering(self):
        from search import is_sing_along_track

        # Positive cases (original vocals with lyrics)
        self.assertTrue(is_sing_along_track("Adele - Rolling in the Deep (Lyrics)"))
        self.assertTrue(is_sing_along_track("Queen - Bohemian Rhapsody (Official Lyric Video)"))
        self.assertTrue(is_sing_along_track("Journey - Don't Stop Believin' [Lyrics on screen]"))
        self.assertTrue(is_sing_along_track("Disney Sing Along - Under the Sea"))

        # Negative cases (pure karaoke, instrumental, backing track, or non-music)
        self.assertFalse(is_sing_along_track("Adele - Rolling in the Deep (Karaoke Version)"))
        self.assertFalse(is_sing_along_track("Queen - Bohemian Rhapsody Instrumental with lyrics"))
        self.assertFalse(is_sing_along_track("Journey - Don't Stop Believin' (Backing Track)"))
        self.assertFalse(is_sing_along_track("Ed Sheeran - Perfect (Off Vocal)"))
        self.assertFalse(is_sing_along_track("Taylor Swift - Love Story (Sing King Karaoke)"))
        self.assertFalse(is_sing_along_track("Beatles - Let It Be (Vocal Coach Reaction)"))
        self.assertFalse(is_sing_along_track("How to sing Rolling in the Deep tutorial"))

    def test_sing_along_queue_and_library(self):
        from config import HOST_TOKEN, get_sing_along_dir, get_tracks_dir
        from database import get_track_by_id, update_track_download_status

        # 1. Queue a normal karaoke track
        r1 = self.client.post("/api/queue", json={
            "video_id": "sa_test_karaoke_01",
            "title": "Normal Karaoke Song",
            "artist": "Standard Artist",
            "singer_token": "tok_sa_singer",
            "is_sing_along": False
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r1.status_code, 200)

        # 2. Queue a sing-along track
        r2 = self.client.post("/api/queue", json={
            "video_id": "sa_test_sing_along_02",
            "title": "Sing Along Song",
            "artist": "Vocal Artist",
            "singer_token": "tok_sa_singer",
            "is_sing_along": True
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r2.status_code, 200)

        # 3. Check /api/queue returns correct is_sing_along flags
        q_res = self.client.get("/api/queue").json()
        item_normal = next((q for q in q_res if q["video_id"] == "sa_test_karaoke_01"), None)
        item_sa = next((q for q in q_res if q["video_id"] == "sa_test_sing_along_02"), None)
        self.assertIsNotNone(item_normal)
        self.assertIsNotNone(item_sa)
        self.assertEqual(item_normal["is_sing_along"], 0)
        self.assertEqual(item_sa["is_sing_along"], 1)

        # 4. Check downloader directory destination logic
        track_normal = get_track_by_id(item_normal["track_id"])
        track_sa = get_track_by_id(item_sa["track_id"])
        self.assertEqual(track_normal["is_sing_along"], 0)
        self.assertEqual(track_sa["is_sing_along"], 1)

        # Mock download status and verify library separation
        update_track_download_status(track_normal["id"], "ready", str(get_tracks_dir() / "Standard Artist" / "Normal.mp4"))
        update_track_download_status(track_sa["id"], "ready", str(get_sing_along_dir() / "Vocal Artist" / "SingAlong.mp4"))

        # Verify /api/library with sing_along=false returns only normal karaoke track
        lib_normal = self.client.get("/api/library?sing_along=false").json()
        self.assertTrue(any(t["video_id"] == "sa_test_karaoke_01" for t in lib_normal["tracks"]))
        self.assertFalse(any(t["video_id"] == "sa_test_sing_along_02" for t in lib_normal["tracks"]))

        # Verify /api/library with sing_along=true returns only sing-along track
        lib_sa = self.client.get("/api/library?sing_along=true").json()
        self.assertFalse(any(t["video_id"] == "sa_test_karaoke_01" for t in lib_sa["tracks"]))
        self.assertTrue(any(t["video_id"] == "sa_test_sing_along_02" for t in lib_sa["tracks"]))

    def test_default_paths(self):
        from config import HOST_TOKEN
        res = self.client.get("/api/host/default-paths", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("default_music_dir", data)
        self.assertIn("user_karaoke_parent", data)
        self.assertIn("db_dir", data)
        self.assertTrue(data["default_music_dir"].endswith("Music/Karaoke/Tiki") or "Music\\Karaoke\\Tiki" in data["default_music_dir"])

    def test_import_local_library(self):
        from config import HOST_TOKEN
        import tempfile
        from database import search_local_tracks

        # Create temporary folder with mock karaoke video files
        with tempfile.TemporaryDirectory(prefix="tiki_mock_import_") as tmp_import:
            f1 = Path(tmp_import) / "Journey - Don't Stop Believin' (Karaoke).mp4"
            f1.write_bytes(b"dummy mp4 content")
            f2 = Path(tmp_import) / "subfolder"
            f2.mkdir(parents=True, exist_ok=True)
            f2_file = f2 / "Bon Jovi - Livin' on a Prayer.mkv"
            f2_file.write_bytes(b"dummy mkv content")

            # Post import request
            payload = {
                "folder_path": tmp_import,
                "recursive": True,
                "is_sing_along": False
            }
            res = self.client.post("/api/host/import-local-library", json=payload, headers={"X-Host-Token": HOST_TOKEN})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["imported_count"], 2)
            self.assertEqual(data["total_found"], 2)

            # Verify local FTS search finds the imported songs
            journey_results = search_local_tracks("Believin")
            self.assertTrue(len(journey_results) > 0)
            self.assertEqual(journey_results[0]["artist"], "Journey")

            bon_jovi_results = search_local_tracks("Prayer")
            self.assertTrue(len(bon_jovi_results) > 0)
            self.assertEqual(bon_jovi_results[0]["artist"], "Bon Jovi")

    def test_legal_guide_page(self):
        res = self.client.get("/legal")
        self.assertEqual(res.status_code, 200)
        content = res.text
        self.assertIn("About & Bring-Your-Own-Media Guide", content)
        self.assertIn("karaoke-version.com", content)
        self.assertIn("sunflykaraoke.com", content)
        self.assertIn("PolyForm Noncommercial License", content)
        self.assertIn("Bring Your Own Media (BYOM)", content)

    def test_strict_commercial_byom_lockdown(self):
        from config import HOST_TOKEN
        from database import (
            save_track, set_setting, get_setting, add_filler_track,
            get_next_vibe_filler_track, import_local_filler_track
        )
        from search import tiered_search

        # 1. Verify fresh install default is false (YouTube search ON by default)
        self.assertEqual(self.default_strict_mode, "false")

        # 2. Check host authentication
        res = self.client.get("/api/host/strict-mode")
        self.assertEqual(res.status_code, 403)

        # 3. Toggle strict mode ON
        res = self.client.post("/api/host/strict-mode", json={"enabled": True}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["strict_byom_only"])
        self.assertEqual(get_setting("strict_byom_only"), "true")

        # 4. Create an imported track and a youtube track
        tid_imported = save_track("local_acdc_back_in_black", "Back In Black", "AC/DC", "AC/DC - Back In Black", media_source="imported")
        from database import update_track_download_status
        update_track_download_status(tid_imported, "ready", "/dummy/path/acdc.mp4")

        tid_youtube = save_track("yt_stream_track_01", "Thunderstruck", "AC/DC", "AC/DC - Thunderstruck", media_source="youtube")

        # 5. Intermission Lockdown: Unlicensed YouTube filler tracks are excluded under strict mode
        add_filler_track("yt_filler_bad_01", "Bad Stream Clip", "Bad Artist", "Bad Stream Clip", is_downloaded=1, media_source="youtube")
        pick = get_next_vibe_filler_track(mode="pure_filler")
        self.assertIsNone(pick)

        # 6. Licensed / imported filler video is successfully picked!
        import_local_filler_track("/dummy/path/promoonly_dance.mp4", "Licensed Dance Video", "Pool DJ", duration=210)
        pick_imported = get_next_vibe_filler_track(mode="pure_filler")
        self.assertIsNotNone(pick_imported)
        self.assertEqual(pick_imported["artist"], "Pool DJ")

        # 7. Search under strict mode: only imported track is returned, and search skips online
        results = tiered_search("Black")
        self.assertTrue(results.get("strict_mode", False))
        self.assertEqual(len(results["youtube_results"]), 0)
        self.assertEqual(len(results["local_results"]), 1)
        self.assertEqual(results["local_results"][0]["artist"], "AC/DC")

        # 8. Enqueue rejection for youtube track
        singer_token = "strict_singer_test"
        res_yt = self.client.post("/api/queue", json={
            "track_id": tid_youtube,
            "singer_token": singer_token,
            "video_id": "yt_stream_track_01",
            "title": "Thunderstruck",
            "artist": "AC/DC"
        })
        self.assertEqual(res_yt.status_code, 403)
        self.assertIn("Curated Local Library Mode Active", res_yt.json()["detail"])

        # 9. Enqueue success for imported track
        res_imp = self.client.post("/api/queue", json={
            "track_id": tid_imported,
            "singer_token": singer_token,
            "video_id": "local_acdc_back_in_black",
            "title": "Back In Black",
            "artist": "AC/DC"
        })
        self.assertEqual(res_imp.status_code, 200)
        self.assertEqual(res_imp.json()["status"], "ok")

        # 10. Reset strict mode to false
        self.client.post("/api/host/strict-mode", json={"enabled": False}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(get_setting("strict_byom_only"), "false")

    def test_setup_redirects_to_host_and_default_windowed(self):
        """Verifies setup save redirects to Host Desk and default kiosk_mode is False."""
        from config import DEFAULT_CONFIG
        self.assertFalse(DEFAULT_CONFIG.get("kiosk_mode", True))

        from config import HOST_TOKEN
        res = self.client.post("/api/setup", json={
            "bar_name": "Test Lounge",
            "host_pin": "1234",
            "kiosk_mode": False
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("/host", data["redirect"])

    def test_control_hub_module_and_safe_logger(self):
        """Verifies control_hub class structure and main SafeLogger."""
        import control_hub
        self.assertTrue(hasattr(control_hub, "TikiControlHub"))
        
        hub = control_hub.TikiControlHub(
            urls={"stage": "http://127.0.0.1:8000/stage", "host": "http://127.0.0.1:8000/host"},
            bar_name="Unit Test Lounge"
        )
        self.assertEqual(hub.bar_name, "Unit Test Lounge")

        from main import SafeLogger
        import io
        buf = io.StringIO()
        logger = SafeLogger(filepath=None, original_stream=buf)
        logger.write("Test log output\n")
        logger.flush()
        self.assertEqual(buf.getvalue(), "Test log output\n")

    def test_setup_display_settings_and_recordings_path(self):
        """Verifies multi-monitor display configuration persistence and performance_recordings default path."""
        import json
        from config import get_library_dir, get_default_recordings_dir, get_recordings_dir, load_config, HOST_TOKEN
        from database import get_setting
        from main import configure_firefox_profile

        # 1. Performance recordings path validation
        default_recs = get_default_recordings_dir()
        self.assertEqual(default_recs.name, "performance_recordings")
        self.assertEqual(default_recs.parent.resolve(), get_library_dir().resolve())

        # 2. Multi-monitor display settings persistence via /api/setup
        setup_payload = {
            "bar_name": "Multi-Monitor Lounge",
            "stage_display": 1,
            "stage_display_name": "Monitor 1: DP-1 [1920x1080]",
            "stage_display_x": 1920,
            "stage_display_y": 0,
            "stage_display_width": 1920,
            "stage_display_height": 1080,
            "kiosk_mode": False,
            "host_display_enabled": True,
            "host_display": 0,
            "host_display_name": "Monitor 0: DP-2 [2560x1440]",
            "host_display_x": 0,
            "host_display_y": 0,
            "host_display_width": 2560,
            "host_display_height": 1440,
            "host_kiosk_mode": False
        }
        res = self.client.post("/api/setup", json=setup_payload, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(res.status_code, 200)
        
        cfg = load_config()
        self.assertEqual(cfg.get("stage_display"), 1)
        self.assertEqual(cfg.get("stage_display_x"), 1920)
        self.assertEqual(cfg.get("kiosk_mode"), False)
        self.assertEqual(cfg.get("host_display_enabled"), True)
        self.assertEqual(cfg.get("host_display"), 0)
        self.assertEqual(cfg.get("host_display_width"), 2560)
        self.assertEqual(cfg.get("host_kiosk_mode"), False)

        self.assertEqual(get_setting("stage_display_x"), "1920")
        self.assertEqual(get_setting("stage_display_width"), "1920")
        self.assertEqual(get_setting("host_display_enabled"), "true")
        self.assertEqual(get_setting("host_kiosk_mode"), "false")

        # 3. Firefox profile xulstore.json generation
        with tempfile.TemporaryDirectory() as tmp_dir:
            host_prof = Path(tmp_dir) / "firefox_host"
            configure_firefox_profile(
                host_prof,
                screen_x=1920,
                screen_y=0,
                width=1920,
                height=1080,
                is_fullscreen=False
            )
            xul_file = host_prof / "xulstore.json"
            self.assertTrue(xul_file.exists())
            with open(xul_file, "r") as f:
                xul_data = json.load(f)
            win_data = xul_data["chrome://browser/content/browser.xhtml"]["main-window"]
            self.assertEqual(win_data["sizemode"], "normal")
            self.assertEqual(win_data["screenX"], "1920")
            self.assertEqual(win_data["screenY"], "0")
            self.assertEqual(win_data["width"], "1920")
            self.assertEqual(win_data["height"], "1080")

            # Kiosk fullscreen mode test
            stage_prof = Path(tmp_dir) / "firefox_stage"
            configure_firefox_profile(
                stage_prof,
                screen_x=0,
                screen_y=0,
                width=2560,
                height=1440,
                is_fullscreen=True
            )
            with open(stage_prof / "xulstore.json", "r") as f:
                stage_xul = json.load(f)
            stage_win = stage_xul["chrome://browser/content/browser.xhtml"]["main-window"]
            self.assertEqual(stage_win["sizemode"], "fullscreen")

    def test_hotspot_wifi_card_conditional_display(self):
        """Verifies that the Wi-Fi QR card only appears when local hotspot is enabled with configured credentials."""
        import network_helper
        from database import set_setting
        from config import HOST_TOKEN

        # Case 1: remote_mode is cloudflare_quick -> show_wifi_card MUST be False
        set_setting("remote_mode", "cloudflare_quick")
        set_setting("hotspot_enabled", "true")
        set_setting("hotspot_ssid", "MyHotspot")
        set_setting("hotspot_password", "password123")
        self.assertFalse(network_helper.is_hotspot_enabled())

        res = self.client.get("/api/host/network", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["show_wifi_card"])

        stage_res = self.client.get("/stage", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(stage_res.status_code, 200)
        self.assertIn('id="idleWifiQRCard" style="display: none;', stage_res.text)

        # Case 2: remote_mode is local, but hotspot_enabled is false -> show_wifi_card MUST be False
        set_setting("remote_mode", "local")
        set_setting("hotspot_enabled", "false")
        self.assertFalse(network_helper.is_hotspot_enabled())
        res = self.client.get("/api/host/network", headers={"X-Host-Token": HOST_TOKEN})
        self.assertFalse(res.json()["show_wifi_card"])

        # Case 3: remote_mode is local, hotspot_enabled is true, but SSID is blank -> show_wifi_card MUST be False
        set_setting("hotspot_enabled", "true")
        set_setting("hotspot_ssid", "   ")
        self.assertFalse(network_helper.is_hotspot_enabled())
        res = self.client.get("/api/host/network", headers={"X-Host-Token": HOST_TOKEN})
        self.assertFalse(res.json()["show_wifi_card"])

        # Case 4: remote_mode is local, hotspot_enabled is true, and SSID is configured -> show_wifi_card MUST be True
        update_res = self.client.post("/api/host/network/hotspot-config", json={
            "ssid": "BeachLounge_5G",
            "password": "tikitrailpass",
            "enabled": True
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(update_res.status_code, 200)
        data = update_res.json()
        self.assertTrue(data["show_wifi_card"])
        self.assertEqual(data["hotspot_ssid"], "BeachLounge_5G")
        self.assertEqual(data["hotspot_password"], "tikitrailpass")
        self.assertTrue(network_helper.is_hotspot_enabled())

        stage_res_active = self.client.get("/stage", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(stage_res_active.status_code, 200)
        self.assertIn('id="idleWifiQRCard" style="display: block;', stage_res_active.text)
        self.assertIn('BeachLounge_5G', stage_res_active.text)

        # Teardown / restore remote_mode
        set_setting("remote_mode", "cloudflare_quick")
        set_setting("hotspot_enabled", "false")

    def test_marquee_configuration_and_speed_control(self):
        """Verifies stage marquee speed configuration, bounds validation, and Host Desk control."""
        from config import DEFAULT_CONFIG, HOST_TOKEN
        from database import get_setting, set_setting

        # 1. Config default
        self.assertEqual(DEFAULT_CONFIG.get("marquee_scroll_speed"), 85)

        # 2. State endpoint reflects marquee speed
        res = self.client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        state_data = res.json()
        self.assertIn("marquee", state_data)
        self.assertEqual(state_data["marquee"]["speed"], int(get_setting("marquee_scroll_speed", "85")))

        # 3. Post announcement with speed requires auth
        unauth_res = self.client.post("/api/host/marquee", json={
            "message": "Welcome singers!",
            "duration_minutes": 10,
            "speed": 115
        })
        self.assertEqual(unauth_res.status_code, 403)

        # 4. Authorized post announcement with speed updates setting and returns payload
        auth_res = self.client.post("/api/host/marquee", json={
            "message": "Welcome singers!",
            "duration_minutes": 10,
            "speed": 115
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(auth_res.status_code, 200)
        auth_data = auth_res.json()
        self.assertEqual(auth_data["status"], "ok")
        self.assertEqual(auth_data["message"], "Welcome singers!")
        self.assertEqual(auth_data["speed"], 115)
        self.assertEqual(get_setting("marquee_scroll_speed"), "115")

        # 5. Dedicated speed endpoint updates speed independently
        speed_res = self.client.post("/api/host/marquee/speed", json={"speed": 145}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(speed_res.status_code, 200)
        self.assertEqual(speed_res.json()["speed"], 145)
        self.assertEqual(get_setting("marquee_scroll_speed"), "145")

        # 6. Bounding check (clamp between 30 and 300)
        clamp_low = self.client.post("/api/host/marquee/speed", json={"speed": 10}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(clamp_low.json()["speed"], 30)

        clamp_high = self.client.post("/api/host/marquee/speed", json={"speed": 999}, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(clamp_high.json()["speed"], 300)

        # 7. Clear marquee maintains configured speed
        clear_res = self.client.delete("/api/host/marquee", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(clear_res.status_code, 200)
        self.assertTrue(clear_res.json()["cleared"])
        self.assertEqual(clear_res.json()["speed"], 300)

        # Reset to default
        set_setting("marquee_scroll_speed", "85")

    def test_queue_intermission_stability_and_marquee_stacking(self):
        """Verifies that queuing songs during an active intermission countdown or BGM fade
        does not clobber/reset the countdown timer or interrupt transitions."""
        import time
        import app
        from config import HOST_TOKEN
        from database import set_setting

        # Setup: Ensure no song is playing
        set_setting("intermission_seconds", "15")
        fake_end = time.time() + 10.0
        app.current_intermission_end = fake_end

        # Singer A token
        token_a = "singer_test_intermission_a"

        # Enqueue while intermission is running
        res = self.client.post("/api/queue", json={
            "singer_token": token_a,
            "title": "Intermission Guard Song",
            "artist": "Test Artist",
            "video_id": "test_intermission_vid_123",
            "force": True
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(res.status_code, 200)

        # Intermission end must NOT have been overwritten or extended!
        self.assertEqual(app.current_intermission_end, fake_end)

        # Cleanup
        app.current_intermission_end = None

        # Verify host.html template contains transition on ticker and QR
        stage_page = self.client.get("/stage", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(stage_page.status_code, 200)
        self.assertIn("transition: bottom 0.25s ease;", stage_page.text)

    def test_pending_downloads_staging_and_retry_ladder(self):
        from config import HOST_TOKEN
        from database import (
            get_pending_downloads, get_pending_download_by_id,
            update_pending_download_progress, promote_pending_download_to_queue,
            remove_pending_download, check_song_queue_duplicate, get_queue
        )

        singer_token = "tok_pending_test_1"

        # 1. Enqueue an online un-downloaded track
        res = self.client.post("/api/queue", json={
            "singer_token": singer_token,
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "video_id": "online_queen_bohemian",
            "duet_partner": "Freddie",
            "pitch_shift": 2,
            "dedication": "For everyone in the car!"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("is_pending_download"))
        pending_id = data["pending_id"]
        self.assertIsNotNone(pending_id)

        # 2. Verify track is in pending_downloads, NOT in active stage queue
        active_queue = self.client.get("/api/queue").json()
        self.assertFalse(any(q.get("video_id") == "online_queen_bohemian" for q in active_queue))

        pending_list = self.client.get("/api/downloads/pending").json()["pending_downloads"]
        found = next((p for p in pending_list if p["id"] == pending_id), None)
        self.assertIsNotNone(found)
        self.assertEqual(found["title"], "Bohemian Rhapsody")
        self.assertEqual(found["artist"], "Queen")
        self.assertEqual(found["duet_partner"], "Freddie")
        self.assertEqual(found["pitch_shift"], 2)

        # 3. Check duplicate prevention catches pending download
        dup = check_song_queue_duplicate(singer_id=found["singer_id"], video_id="online_queen_bohemian")
        self.assertIsNotNone(dup)
        self.assertTrue(dup["duplicate_found"])
        self.assertEqual(dup["status"], "downloading")

        # 4. Progress update
        update_pending_download_progress(pending_id, 45.5, status="downloading")
        item = get_pending_download_by_id(pending_id)
        self.assertEqual(item["progress"], 45.5)

        # 5. Retry endpoint with lower quality profile
        r_retry = self.client.post(f"/api/download/retry/{pending_id}?quality=medium_360p")
        self.assertEqual(r_retry.status_code, 200)
        item = get_pending_download_by_id(pending_id)
        self.assertEqual(item["quality_profile"], "medium_360p")
        self.assertGreaterEqual(item["retry_count"], 1)

        # 6. Promotion to active queue upon download completion
        new_queue_id = promote_pending_download_to_queue(pending_id)
        self.assertIsNotNone(new_queue_id)

        # Now active queue has it
        active_queue = self.client.get("/api/queue").json()
        promoted = next((q for q in active_queue if q["queue_id"] == new_queue_id), None)
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted["title"], "Bohemian Rhapsody")
        self.assertEqual(promoted["pitch_shift"], 2)

        # Pending downloads list no longer has it
        pending_list = self.client.get("/api/downloads/pending").json()["pending_downloads"]
        self.assertFalse(any(p["id"] == pending_id for p in pending_list))

    def test_starter_packs_3_buttons_and_song_management(self):
        import shutil
        from config import HOST_TOKEN, get_tracks_dir, get_sing_along_dir, get_filler_dir
        from database import (
            save_track, get_track_by_id, add_filler_track, get_filler_track_by_id,
            get_pending_downloads, get_cleanup_candidates
        )
        cookies = {"tiki_host": HOST_TOKEN}

        # 1. Test Party Packs 3-button enqueue (Karaoke, Sing-Along, Music Video)
        r_k = self.client.post("/api/host/party-packs/essentials/download?mode=karaoke", cookies=cookies)
        self.assertEqual(r_k.status_code, 200)
        self.assertEqual(r_k.json()["mode"], "karaoke")
        self.assertGreater(r_k.json()["enqueued"], 0)

        r_sa = self.client.post("/api/host/party-packs/rock_classics/download", json={"mode": "sing_along"}, cookies=cookies)
        self.assertEqual(r_sa.status_code, 200)
        self.assertEqual(r_sa.json()["mode"], "sing_along")

        r_mv = self.client.post("/api/host/filler/install-starter", json={"mode": "music_video"}, cookies=cookies)
        self.assertEqual(r_mv.status_code, 200)
        self.assertEqual(r_mv.json()["mode"], "music_video")

        # 2. Verify priority in pending downloads: singer requests (priority 10) sort before starter packs (priority 100)
        r_singer = self.client.post("/api/singer/register", json={"name": "VIP Guest", "avatar": "⭐", "token": "vip_guest_token"})
        self.assertEqual(r_singer.status_code, 200)

        r_queue = self.client.post("/api/queue", json={
            "singer_token": "vip_guest_token",
            "video_id": "online_urgent_song",
            "title": "Urgent Live Track",
            "artist": "Priority Singer",
            "original_title": "Priority Singer - Urgent Live Track"
        })
        self.assertEqual(r_queue.status_code, 200)

        pending_items = self.client.get("/api/downloads/pending").json()["pending_downloads"]
        self.assertGreater(len(pending_items), 1)
        # The first item must be the singer's request because priority=10 < priority=100
        first_item = pending_items[0]
        self.assertEqual(first_item["singer_name"], "VIP Guest")
        self.assertEqual(first_item["priority"], 10)

        # Starter pack items must have priority=100 and auto_promote=0
        starter_items = [p for p in pending_items if p["priority"] == 100]
        self.assertTrue(len(starter_items) > 0)
        self.assertEqual(starter_items[0]["auto_promote"], 0)

        # 3. Test Metadata Editing and Physical File Renaming
        test_dir = get_tracks_dir() / "TestArtist"
        test_dir.mkdir(parents=True, exist_ok=True)
        dummy_file = test_dir / "OldSong [test_vid_edit].mp4"
        dummy_file.write_text("dummy video content")

        tid = save_track(
            video_id="test_vid_edit",
            title="OldSong",
            artist="TestArtist",
            original_title="TestArtist - OldSong",
            is_sing_along=False,
            is_starter_pack=1,
            starter_pack_name="Test Pack"
        )
        from database import update_track_download_status
        update_track_download_status(tid, "ready", local_path=str(dummy_file))

        # Edit metadata via API
        r_edit = self.client.put(f"/api/host/tracks/{tid}", json={
            "title": "RenamedSong",
            "artist": "NewArtist",
            "is_filler": False
        }, cookies=cookies)
        self.assertEqual(r_edit.status_code, 200)

        updated_track = get_track_by_id(tid)
        self.assertEqual(updated_track["title"], "RenamedSong")
        self.assertEqual(updated_track["artist"], "NewArtist")
        # Check that physical file was renamed
        self.assertFalse(dummy_file.exists())
        new_file = Path(updated_track["local_path"])
        self.assertTrue(new_file.exists())
        self.assertIn("RenamedSong [test_vid_edit].mp4", new_file.name)

        # 4. Test Failsafe Type Conversion (Option A: Physical File Relocation)
        # 4a. Convert from Karaoke to Sing-Along
        r_conv_sa = self.client.put(f"/api/host/tracks/{tid}/convert-type", json={
            "target_type": "sing_along",
            "source_type": "karaoke",
            "is_filler": False
        }, cookies=cookies)
        self.assertEqual(r_conv_sa.status_code, 200)
        t_sa = get_track_by_id(tid)
        self.assertEqual(t_sa["is_sing_along"], 1)
        sa_file = Path(t_sa["local_path"])
        self.assertTrue(sa_file.exists())
        self.assertTrue(str(sa_file).startswith(str(get_sing_along_dir())))

        # 4b. Convert from Sing-Along to Music Video (Filler)
        r_conv_mv = self.client.put(f"/api/host/tracks/{tid}/convert-type", json={
            "target_type": "music_video",
            "source_type": "sing_along",
            "is_filler": False
        }, cookies=cookies)
        self.assertEqual(r_conv_mv.status_code, 200)
        mv_id = r_conv_mv.json()["track_id"]
        f_track = get_filler_track_by_id(mv_id)
        self.assertIsNotNone(f_track)
        mv_file = Path(f_track["local_path"])
        self.assertTrue(mv_file.exists())
        self.assertTrue(str(mv_file).startswith(str(get_filler_dir())))

        # 5. Test Storage Cleanup Candidates Tab Filtering
        r_clean_all = self.client.get("/api/storage/cleanup-candidates?tab=all")
        self.assertEqual(r_clean_all.status_code, 200)
        candidates_all = r_clean_all.json()["candidates"]
        self.assertTrue(any(c["video_id"] == "test_vid_edit" for c in candidates_all))

        r_clean_pack = self.client.get("/api/storage/cleanup-candidates?tab=starter_packs")
        self.assertEqual(r_clean_pack.status_code, 200)
        candidates_pack = r_clean_pack.json()["candidates"]
        found_pack = next((c for c in candidates_pack if c["video_id"] == "test_vid_edit"), None)
        self.assertIsNotNone(found_pack)
        self.assertEqual(found_pack["song_type"], "music_video")
        self.assertEqual(found_pack["is_starter_pack"], 1)

        # Clean up created file
        if mv_file.exists():
            mv_file.unlink()

    def test_starter_packs_repair_and_deduplication(self):
        """Verifies starter crate status endpoint, pack repair deduplication, and artist+title matching."""
        from config import HOST_TOKEN, get_tracks_dir
        from database import save_track, update_track_download_status, find_existing_downloaded_track
        cookies = {"tiki_host": HOST_TOKEN}

        # 1. Test GET /api/host/starter-crate/status
        r_status = self.client.get("/api/host/starter-crate/status", cookies=cookies)
        self.assertEqual(r_status.status_code, 200)
        data = r_status.json()
        self.assertIn("total_tracks", data)
        self.assertIn("music_video_count", data)
        self.assertIn("karaoke_count", data)
        self.assertIn("sing_along_count", data)
        self.assertIn("is_fully_installed", data)

        # 2. Test find_existing_downloaded_track logic
        dummy_dir = get_tracks_dir() / "DedupeArtist"
        dummy_dir.mkdir(parents=True, exist_ok=True)
        dummy_file = dummy_dir / "UniqueSong [dedupe_vid].mp4"
        dummy_file.write_text("mp4 content")

        tid = save_track(
            video_id="dedupe_vid",
            title="UniqueSong",
            artist="DedupeArtist",
            original_title="DedupeArtist - UniqueSong",
            is_sing_along=False
        )
        update_track_download_status(tid, "ready", local_path=str(dummy_file))

        # 2a. Exact and normalized match should succeed
        match = find_existing_downloaded_track("DedupeArtist", "UniqueSong", "karaoke")
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], tid)

        match_case = find_existing_downloaded_track("dedupe artist", "unique song", "karaoke")
        self.assertIsNotNone(match_case)

        # 2b. Same title with DIFFERENT artist must NOT deduplicate
        diff_artist = find_existing_downloaded_track("OtherArtist", "UniqueSong", "karaoke")
        self.assertIsNone(diff_artist)

        # 2c. Same artist with DIFFERENT title must NOT deduplicate
        diff_title = find_existing_downloaded_track("DedupeArtist", "OtherSong", "karaoke")
        self.assertIsNone(diff_title)

        # 2d. If physical file is missing, track must NOT be considered downloaded
        dummy_file.unlink()
        match_deleted = find_existing_downloaded_track("DedupeArtist", "UniqueSong", "karaoke")
        self.assertIsNone(match_deleted)

        # 3. Test pack endpoint returns already_installed counts and flags
        r_packs = self.client.get("/api/host/party-packs", cookies=cookies)
        self.assertEqual(r_packs.status_code, 200)
        packs_data = r_packs.json()
        self.assertIn("packs", packs_data)
        self.assertIn("starter_crate", packs_data)
        if len(packs_data["packs"]) > 0:
            first_pack = packs_data["packs"][0]
            self.assertIn("is_installed_k", first_pack)
            self.assertIn("is_installed_sa", first_pack)
            self.assertIn("is_installed_mv", first_pack)



    def test_browser_detection_and_discovery_api(self):
        """Verifies browser detection API, recommendation flags, and preferred_browser persistence."""
        from config import get_available_browsers, open_url_in_preferred_browser, HOST_TOKEN, load_config
        from database import get_setting

        # 1. Direct config helper detection
        browsers = get_available_browsers()
        self.assertIsInstance(browsers, list)
        self.assertGreaterEqual(len(browsers), 2)

        browser_ids = [b["id"] for b in browsers]
        self.assertIn("firefox", browser_ids)
        self.assertIn("chrome", browser_ids)
        self.assertIn("default", browser_ids)

        ff = next(b for b in browsers if b["id"] == "firefox")
        self.assertIn("Top Pick", ff["badge"])
        if ff["available"]:
            self.assertTrue(ff["recommended"])

        default_b = next(b for b in browsers if b["id"] == "default")
        self.assertTrue(default_b["available"])

        # 2. Endpoint authentication check
        r_unauth = self.client.get("/api/setup/browsers")
        self.assertEqual(r_unauth.status_code, 403)

        r_auth = self.client.get("/api/setup/browsers", headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(r_auth.status_code, 200)
        api_browsers = r_auth.json()
        self.assertEqual(len(api_browsers), len(browsers))

        # 3. Setup persistence with preferred_browser
        res = self.client.post("/api/setup", json={
            "bar_name": "Browser Selection Lounge",
            "preferred_browser": "firefox"
        }, headers={"X-Host-Token": HOST_TOKEN})
        self.assertEqual(res.status_code, 200)

        cfg = load_config()
        self.assertEqual(cfg.get("preferred_browser"), "firefox")
        self.assertEqual(get_setting("preferred_browser"), "firefox")

        # 4. Safe URL opener invocation
        res_open = open_url_in_preferred_browser("http://127.0.0.1:8000/setup")
        self.assertTrue(res_open)

if __name__ == "__main__":
    unittest.main()




