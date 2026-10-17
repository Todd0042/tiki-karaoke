import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

# Create isolated test environment before importing app/config
test_dir = tempfile.mkdtemp(prefix="tiki_api_test_")
os.environ["TIKI_DATA_DIR"] = test_dir

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi.testclient import TestClient
from app import app
from database import init_db

class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(test_dir, ignore_errors=True)

    def setUp(self):
        from database import get_db
        conn = get_db()
        with conn:
            conn.execute("DELETE FROM queue")
            conn.execute("DELETE FROM history")
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
        r = self.client.get("/qr/public")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")

        r = self.client.get("/qr/host")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")

    def test_singer_and_queue_flow(self):
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

        # 5. Play item
        r = self.client.post(f"/api/queue/{q_data['queue_id']}/play")
        self.assertEqual(r.status_code, 200)

        # 6. Complete item
        r = self.client.post(f"/api/queue/{q_data['queue_id']}/complete")
        self.assertEqual(r.status_code, 200)

        # 7. Check singer history
        r = self.client.get("/api/singer/test_singer_token/history")
        self.assertEqual(r.status_code, 200)
        history = r.json()
        self.assertGreaterEqual(len(history), 1)

    def test_settings_api(self):
        r = self.client.post("/api/settings", json={"tts_enabled": "true"})
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tts_enabled"], "true")

    def test_storage_inspect_and_setup(self):
        # 1. Inspect valid path
        r = self.client.post("/api/setup/inspect-path", json={"path": "."})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("valid"))
        self.assertGreater(data.get("free_gb", 0), 0)

        # 2. Setup page renders with default directories
        r = self.client.get("/setup")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Storage & Media Library", r.text)

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
        # 1. Test displays discovery API
        r = self.client.get("/api/setup/displays")
        self.assertEqual(r.status_code, 200)
        displays = r.json()
        self.assertIsInstance(displays, list)
        self.assertGreaterEqual(len(displays), 1)
        self.assertIn("id", displays[0])
        self.assertIn("name", displays[0])
        self.assertIn("width", displays[0])
        self.assertIn("height", displays[0])

        # 2. Test audio sinks discovery API
        r = self.client.get("/api/setup/audio-devices")
        self.assertEqual(r.status_code, 200)
        sinks = r.json()
        self.assertIsInstance(sinks, list)
        self.assertGreaterEqual(len(sinks), 1)
        self.assertIn("id", sinks[0])
        self.assertIn("description", sinks[0])

        # 3. Test test-display broadcast API
        r = self.client.post("/api/setup/test-display", json={"badge": "📺 MAIN STAGE DISPLAY"})
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
        # Queue a song
        r = self.client.post("/api/queue", json={
            "video_id": "v_stop_test",
            "title": "Stop Test Song",
            "artist": "Stop Artist",
            "singer_token": "tok_stop_host"
        })
        self.assertEqual(r.status_code, 200)
        q_id = r.json()["queue_id"]

        # Play it
        r = self.client.post(f"/api/queue/{q_id}/play")
        self.assertEqual(r.status_code, 200)

        # Stop stage
        r = self.client.post("/api/host/stop")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

        # Verify state: playing is None, next is the pending song
        r = self.client.get("/api/state")
        self.assertEqual(r.status_code, 200)
        state = r.json()
        self.assertIsNone(state["playing"])
        self.assertIsNotNone(state["next"])
        self.assertEqual(state["next"]["queue_id"], q_id)

        # Skip track
        r = self.client.post("/api/host/skip")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

        # Verify queue is now empty of pending tracks
        r = self.client.get("/api/state")
        state = r.json()
        self.assertIsNone(state["playing"])
        self.assertIsNone(state["next"])

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

        # Queue Song A twice with pitch +2
        self.client.post("/api/queue", json={
            "video_id": "v_song_a",
            "title": "Song A",
            "artist": "Artist Alpha",
            "singer_token": token,
            "pitch_shift": 2,
            "force": True
        })
        self.client.post("/api/queue", json={
            "video_id": "v_song_a",
            "title": "Song A",
            "artist": "Artist Alpha",
            "singer_token": token,
            "pitch_shift": 2,
            "force": True
        })

        # Queue Song B once with pitch -1
        self.client.post("/api/queue", json={
            "video_id": "v_song_b",
            "title": "Song B",
            "artist": "Artist Beta",
            "singer_token": token,
            "pitch_shift": -1,
            "force": True
        })

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

if __name__ == "__main__":
    unittest.main()


