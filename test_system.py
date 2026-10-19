import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

# Create isolated test environment before importing database/config
test_dir = tempfile.mkdtemp(prefix="tiki_sys_test_")
os.environ["TIKI_DATA_DIR"] = test_dir

# Add current dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import (
    init_db, get_or_create_singer, get_singer_by_token,
    save_track, get_track_by_id, search_local_tracks,
    enqueue_song, get_queue, set_queue_status, remove_from_queue,
    get_singer_history, get_party_wrapped_stats, get_setting, set_setting,
    get_browsable_tracks, get_unique_downloaded_artists, get_cleanup_candidates, prune_local_track,
    set_session_start_time, get_session_start_time, get_taken_avatars_for_name
)
from search import normalize_title_artist, extract_attributes, deduplicate_and_group, tiered_search
from downloader import sanitize_filename, normalize_audio_loudness, update_singer_m3u_playlist
from config import TRACKS_DIR, PLAYLISTS_DIR

class TestTikiKaraoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

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

    def test_01_singer_registration(self):
        token = "test_token_123"
        singer = get_or_create_singer(token, "Sarah", "🦩", "192.168.1.50")
        self.assertEqual(singer["name"], "Sarah")
        self.assertEqual(singer["avatar"], "🦩")

        retrieved = get_singer_by_token(token)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "Sarah")

    def test_02_track_and_fts5_search(self):
        track_id = save_track(
            video_id="test_vid_001",
            title="Dancing Queen",
            artist="ABBA",
            original_title="ABBA - Dancing Queen (Karaoke Version) [Sing King]",
            channel="Sing King",
            duration=230
        )
        self.assertGreater(track_id, 0)

        # Mark as downloaded for local search
        from database import update_track_download_status
        update_track_download_status(track_id, "ready", local_path="/dummy/path.mp4", normalized_lufs=-16.0)

        results = search_local_tracks("Dancing")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Dancing Queen")
        self.assertEqual(results[0]["artist"], "ABBA")

    def test_03_title_normalization_and_attributes(self):
        raw = "Queen - Bohemian Rhapsody (Karaoke Version with Guide Melody) [Sing King] 4K"
        artist, title = normalize_title_artist(raw, "Sing King")
        self.assertEqual(artist, "Queen")
        self.assertEqual(title, "Bohemian Rhapsody")

        attrs = extract_attributes(raw)
        self.assertIn("With Guide Melody", attrs)

        raw_key = "Journey - Don't Stop Believin' (Lower Key -2 Semitones Karaoke)"
        attrs_key = extract_attributes(raw_key)
        self.assertTrue(any("Key -2" in a or "Lower Key" in a for a in attrs_key))

    def test_04_deduplication_and_grouping(self):
        raw_entries = [
            {"id": "v1", "title": "Journey - Don't Stop Believin' (Karaoke Version)", "uploader": "Sing King", "duration": 250},
            {"id": "v2", "title": "Don't Stop Believin' - Journey | Karaoke | KaraFun", "uploader": "KaraFun", "duration": 252},
            {"id": "v3", "title": "ABBA - Waterloo (Karaoke)", "uploader": "Sing King", "duration": 170}
        ]
        groups = deduplicate_and_group(raw_entries)
        # Journey entries should be clustered into a single group with 2 versions
        journey_group = next((g for g in groups if "Journey" in g["artist"] or "Don't Stop" in g["title"]), None)
        self.assertIsNotNone(journey_group)
        self.assertEqual(len(journey_group["versions"]), 2)

    def test_05_queue_and_fair_rotation(self):
        s1 = get_or_create_singer("s_rr_1", "Scott_FairTest", "🌴")
        s2 = get_or_create_singer("s_rr_2", "Andy_FairTest", "🌺")

        t1 = save_track("v_a1", "Song A1", "Artist A", "Artist A - Song A1")
        t2 = save_track("v_a2", "Song A2", "Artist A", "Artist A - Song A2")
        t3 = save_track("v_b1", "Song B1", "Artist B", "Artist B - Song B1")

        # Scott queues Song A1 and Song A2
        q_a1 = enqueue_song(t1, s1["id"], pitch_shift=-2)
        q_a2 = enqueue_song(t2, s1["id"], pitch_shift=0)

        # Scott's Song A1 starts playing
        set_queue_status(q_a1, "playing")

        # Andy queues Song B1 while Scott is playing
        q_b1 = enqueue_song(t3, s2["id"], pitch_shift=1)

        # In fair round-robin:
        # Index 0: Scott's Song A1 (currently playing)
        # Index 1: Andy's Song B1 (Round 1 for Andy, MUST be next up!)
        # Index 2: Scott's Song A2 (Round 2 for Scott)
        queue = get_queue()
        self.assertEqual(len(queue), 3)
        self.assertEqual(queue[0]["queue_id"], q_a1)
        self.assertEqual(queue[0]["status"], "playing")
        self.assertEqual(queue[1]["queue_id"], q_b1) # Andy moved up!
        self.assertEqual(queue[2]["queue_id"], q_a2) # Scott's 2nd song

        # Complete Scott's song
        set_queue_status(q_a1, "completed")
        queue_after = get_queue()
        self.assertEqual(len(queue_after), 2)
        self.assertEqual(queue_after[0]["queue_id"], q_b1) # Andy is still next up
        self.assertEqual(queue_after[1]["queue_id"], q_a2)

        history = get_singer_history(s1["id"])
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["title"], "Song A1")

    def test_06_loudnorm_ffmpeg_helper(self):
        # Create a tiny test audio file and test normalization filter
        test_in = TRACKS_DIR / "test_in.mp4"
        test_out = TRACKS_DIR / "test_out.mp4"
        
        os.system(f'ffmpeg -y -f lavfi -i "sine=frequency=440:duration=1" -c:a aac "{test_in}" > /dev/null 2>&1')
        self.assertTrue(test_in.exists())

        success = normalize_audio_loudness(test_in, test_out, target_lufs=-16.0)
        self.assertTrue(success)
        self.assertTrue(test_out.exists())

        # Cleanup test files
        if test_in.exists(): test_in.unlink()
        if test_out.exists(): test_out.unlink()

    def test_07_m3u_playlist_generation(self):
        update_singer_m3u_playlist("TestSinger", "/path/to/song.mp4", "Super Trouper", "ABBA")
        m3u_file = PLAYLISTS_DIR / "TestSinger.m3u8"
        self.assertTrue(m3u_file.exists())
        content = m3u_file.read_text()
        self.assertIn("ABBA - Super Trouper", content)
        self.assertIn("/path/to/song.mp4", content)
        m3u_file.unlink()

    def test_08_settings(self):
        set_setting("tts_enabled", "true")
        self.assertEqual(get_setting("tts_enabled"), "true")
        set_setting("tts_enabled", "false")
        self.assertEqual(get_setting("tts_enabled"), "false")

    def test_09_play_counts_and_browsable_tracks(self):
        from database import update_track_download_status, get_db
        # Insert test tracks with distinct play counts and last_played
        t1 = save_track("v_gem", "Old Gem", "Queen", "Queen - Old Gem")
        t2 = save_track("v_pop", "Super Hit", "Queen", "Queen - Super Hit")
        t3 = save_track("v_new", "Fresh Track", "Beatles", "Beatles - Fresh Track")

        update_track_download_status(t1, "ready", local_path="/dummy/gem.mp4")
        update_track_download_status(t2, "ready", local_path="/dummy/pop.mp4")
        update_track_download_status(t3, "ready", local_path="/dummy/new.mp4")

        # Manually set play_count & last_played for testing algorithms
        conn = get_db()
        with conn:
            conn.execute("UPDATE tracks SET play_count = 6, last_played = '2026-01-01 12:00:00' WHERE id = ?", (t1,))
            conn.execute("UPDATE tracks SET play_count = 10, last_played = '2026-10-15 12:00:00' WHERE id = ?", (t2,))
            conn.execute("UPDATE tracks SET play_count = 0, last_played = NULL WHERE id = ?", (t3,))
        conn.close()

        # Test Popularity sort
        pop_res = get_browsable_tracks(page=1, limit=10, sort="popularity")
        self.assertGreaterEqual(pop_res["total"], 3)
        track_ids = [t["id"] for t in pop_res["tracks"]]
        # Super Hit (10 plays) should come before Old Gem (6 plays)
        self.assertIn(t2, track_ids)
        self.assertIn(t1, track_ids)
        self.assertLess(track_ids.index(t2), track_ids.index(t1))

        # Test Bring It Back (5+ plays, sorted by oldest last_played)
        bring_res = get_browsable_tracks(page=1, limit=10, sort="bring_back")
        bring_ids = [t["id"] for t in bring_res["tracks"]]
        self.assertIn(t1, bring_ids)
        # Old Gem played back in Jan 2026 should come before Super Hit played in Oct 2026
        if t2 in bring_ids:
            self.assertLess(bring_ids.index(t1), bring_ids.index(t2))

        # Test Artist Filter & Unique Artists
        artists = get_unique_downloaded_artists()
        self.assertIn("Queen", artists)
        self.assertIn("Beatles", artists)

        queen_res = get_browsable_tracks(page=1, limit=10, artist="Queen")
        for trk in queen_res["tracks"]:
            self.assertEqual(trk["artist"], "Queen")

    def test_10_cleanup_and_storage_pruning(self):
        from database import update_track_download_status, get_track_by_id
        # Create a real dummy file in TRACKS_DIR
        dummy_file = TRACKS_DIR / "cleanup_test.mp4"
        dummy_file.write_text("dummy video content for storage test")

        t_clean = save_track("v_clean", "Delete Me", "Temp Artist", "Temp Artist - Delete Me")
        update_track_download_status(t_clean, "ready", local_path=str(dummy_file))

        # Verify candidate is listed
        candidates = get_cleanup_candidates(sort_by="oldest_played", limit=10)
        c_ids = [c["id"] for c in candidates]
        self.assertIn(t_clean, c_ids)

        candidate = next(c for c in candidates if c["id"] == t_clean)
        self.assertGreater(candidate["size_bytes"], 0)

        # Prune local track
        success = prune_local_track(t_clean)
        self.assertTrue(success)
        self.assertFalse(dummy_file.exists())

        # Verify database record preserved metadata, but marked is_downloaded = 0
        track = get_track_by_id(t_clean)
        self.assertIsNotNone(track)
        self.assertEqual(track["is_downloaded"], 0)
        self.assertEqual(track["local_path"], "")
        self.assertEqual(track["title"], "Delete Me")

    def test_11_duplicate_song_detection_and_duet_partner(self):
        from database import check_song_queue_duplicate, add_duet_partner
        s_scott = get_or_create_singer("s_scott_dup", "ScottDup", "🍹")
        s_andy = get_or_create_singer("s_andy_dup", "AndyDup", "🦜")

        t_bohemian = save_track("v_boh_01", "Bohemian Rhapsody", "Queen", "Queen - Bohemian Rhapsody")
        t_bohemian_alt = save_track("v_boh_alt", "Bohemian Rhapsody", "Queen", "Queen - Bohemian Rhapsody (Karaoke)")

        # 1. No duplicate initially
        dup = check_song_queue_duplicate(s_scott["id"], track_id=t_bohemian)
        self.assertIsNone(dup)

        # 2. Scott queues Bohemian Rhapsody
        q_id = enqueue_song(t_bohemian, s_scott["id"])

        # 3. Andy checks duplicate by exact track_id
        dup_andy = check_song_queue_duplicate(s_andy["id"], track_id=t_bohemian)
        self.assertIsNotNone(dup_andy)
        self.assertEqual(dup_andy["match_type"], "other_singer")
        self.assertEqual(dup_andy["existing_singer_name"], "ScottDup")
        self.assertFalse(dup_andy["is_self"])
        self.assertEqual(dup_andy["position_in_line"], 1)

        # 4. Andy checks duplicate by title & artist (alternate video_id)
        dup_alt = check_song_queue_duplicate(s_andy["id"], video_id=t_bohemian_alt, title="Bohemian Rhapsody", artist="Queen")
        self.assertIsNotNone(dup_alt)
        self.assertEqual(dup_alt["match_type"], "other_singer")

        # 5. Scott checks duplicate on own song
        dup_self = check_song_queue_duplicate(s_scott["id"], track_id=t_bohemian)
        self.assertIsNotNone(dup_self)
        self.assertEqual(dup_self["match_type"], "self")
        self.assertTrue(dup_self["is_self"])

        # 6. Song starts playing
        set_queue_status(q_id, "playing")
        dup_playing = check_song_queue_duplicate(s_andy["id"], track_id=t_bohemian)
        self.assertIsNotNone(dup_playing)
        self.assertEqual(dup_playing["match_type"], "playing")

        # 7. Add duet partner
        res = add_duet_partner(q_id, "Andy")
        self.assertEqual(res["duet_partner"], "Andy")

        # 8. Add second duet partner
        res2 = add_duet_partner(q_id, "Sarah")
        self.assertEqual(res2["duet_partner"], "Andy & Sarah")

        # 9. Duplicate addition doesn't repeat
        res3 = add_duet_partner(q_id, "Andy")
        self.assertEqual(res3["duet_partner"], "Andy & Sarah")

    def test_12_session_aware_singer_persistence(self):
        # 1. Establish session 1 in the past
        set_session_start_time("2026-10-01 20:00:00")
        s_past = get_or_create_singer("token_old_testsinger", "TestSinger", "🌴", "192.168.1.10")
        self.assertEqual(s_past["name"], "TestSinger")
        self.assertEqual(s_past["avatar"], "🌴")
        original_singer_id = s_past["id"]

        # Simulate last_seen from that past session
        from database import get_db
        conn = get_db()
        with conn:
            conn.execute("UPDATE singers SET last_seen = '2026-10-01 20:05:00' WHERE id = ?", (original_singer_id,))
        conn.close()

        # Advance session start time to tonight
        set_session_start_time("2026-10-18 22:00:00")

        # 2. Before TestSinger joins tonight, check taken avatars for "TestSinger"
        taken = get_taken_avatars_for_name("TestSinger", exclude_token="token_new_testsinger", client_ip="192.168.1.50")
        # TestSinger was last seen in session 1, so 🌴 is NOT taken tonight!
        self.assertNotIn("🌴", taken)

        # 3. TestSinger joins on a new phone with a brand new token
        s_reclaimed = get_or_create_singer("token_new_testsinger", "TestSinger", "🌴", "192.168.1.50")
        # Should reclaim the original singer ID!
        self.assertEqual(s_reclaimed["id"], original_singer_id)
        self.assertEqual(s_reclaimed["token"], "token_new_testsinger")
        self.assertEqual(s_reclaimed["name"], "TestSinger")
        self.assertEqual(s_reclaimed["avatar"], "🌴")

        # 4. Now TestSinger is actively in tonight's session.
        # Another guest tries to take "TestSinger" with "🌴" from a different IP
        taken_tonight = get_taken_avatars_for_name("TestSinger", exclude_token="guest_token", client_ip="192.168.1.99")
        self.assertIn("🌴", taken_tonight)

        with self.assertRaises(ValueError):
            get_or_create_singer("guest_token", "TestSinger", "🌴", "192.168.1.99", is_host=False)

        # 5. Host on host machine or is_host can always reconnect / reclaim
        s_host = get_or_create_singer("token_host_refresh", "TestSinger", "🌴", "127.0.0.1", is_host=True)
        self.assertEqual(s_host["id"], original_singer_id)
        self.assertEqual(s_host["token"], "token_host_refresh")

    def test_quality_profiles_format(self):
        from downloader import get_ydl_video_format
        import config

        # Test "inept" profile
        cfg = config.load_config()
        original_profile = cfg.get("video_quality_profile", "pro")
        try:
            config.save_config({"video_quality_profile": "inept"})
            fmt_inept = get_ydl_video_format()
            self.assertIn("height<=480", fmt_inept)

            config.save_config({"video_quality_profile": "pro"})
            fmt_pro = get_ydl_video_format()
            self.assertIn("height<=1080", fmt_pro)
        finally:
            config.save_config({"video_quality_profile": original_profile})

    def test_bgm_90min_cooldown_and_era_scoring(self):
        from database import add_filler_track, get_next_vibe_filler_track, save_track, enqueue_song, get_db

        # Add two filler tracks
        tid_nirvana = add_filler_track("nirv123", "Smells Like Teen Spirit", "Nirvana", "Nirvana - Smells Like Teen Spirit", genre="Rock", release_year=1991)
        tid_queen = add_filler_track("queen123", "Bohemian Rhapsody", "Queen", "Queen - Bohemian Rhapsody", genre="Rock", release_year=1975)

        # Record Nirvana as having been sung 15 minutes ago
        track_id = save_track("nirv_karaoke", "Smells Like Teen Spirit", "Nirvana", "Nirvana - Smells Like Teen Spirit")
        singer = get_or_create_singer("token_sing", "Rocker", "🎸", "127.0.0.1")

        # Mark both as downloaded / ready and insert history
        conn = get_db()
        conn.execute("UPDATE filler_tracks SET download_status = 'ready', is_downloaded = 1")
        conn.execute("""
            INSERT INTO history (track_id, singer_id, played_at)
            VALUES (?, ?, datetime('now', '-15 minutes'))
        """, (track_id, singer["id"]))
        conn.commit()
        conn.close()

        # When picking filler, Nirvana should be in 90-minute cooldown, so Queen is selected!
        pick = get_next_vibe_filler_track(last_track_artist="Bush", last_track_genre="Rock", last_track_year=1994)
        self.assertIsNotNone(pick)
        self.assertEqual(pick["artist"], "Queen")
        self.assertEqual(pick["title"], "Bohemian Rhapsody")

    def test_starter_filler_crate_manifest(self):
        from scripts.seed_filler_crate import STARTER_FILLER_TRACKS
        self.assertEqual(len(STARTER_FILLER_TRACKS), 15)
        for t in STARTER_FILLER_TRACKS:
            self.assertTrue(t["video_id"])
            self.assertTrue(t["artist"])
            self.assertTrue(t["title"])
            self.assertTrue(t["genre"])
            self.assertGreater(t["release_year"], 1960)

    def test_ytdlp_status_endpoint(self):
        from app import host_get_ytdlp_status
        from config import HOST_TOKEN
        from starlette.requests import Request
        import asyncio

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/host/system/ytdlp-status",
            "headers": [(b"cookie", f"tiki_host={HOST_TOKEN}".encode())],
        }
        req = Request(scope)
        res = asyncio.run(host_get_ytdlp_status(req))
        self.assertEqual(res["status"], "ok")
        self.assertTrue(bool(res["current_version"]))

    def test_av_sync_endpoints(self):
        from app import host_get_av_sync, host_set_av_sync, AVSyncPayload
        from config import HOST_TOKEN
        from starlette.requests import Request
        import asyncio

        # 1. Test GET av-sync default
        scope_get = {
            "type": "http",
            "method": "GET",
            "path": "/api/host/av-sync",
            "headers": [(b"cookie", f"tiki_host={HOST_TOKEN}".encode())],
        }
        res_get = asyncio.run(host_get_av_sync(Request(scope_get)))
        self.assertEqual(res_get["status"], "ok")
        self.assertIsInstance(res_get["offset_ms"], int)

        # 2. Test POST av-sync set offset
        scope_post = {
            "type": "http",
            "method": "POST",
            "path": "/api/host/av-sync",
            "headers": [(b"cookie", f"tiki_host={HOST_TOKEN}".encode())],
        }
        res_post = asyncio.run(host_set_av_sync(AVSyncPayload(offset_ms=80), Request(scope_post)))
        self.assertEqual(res_post["status"], "ok")
        self.assertEqual(res_post["offset_ms"], 80)

        # Verify persisted
        res_get2 = asyncio.run(host_get_av_sync(Request(scope_get)))
        self.assertEqual(res_get2["offset_ms"], 80)

        # 3. Test clamping boundary (e.g. 999 -> clamped to 500)
        res_clamp = asyncio.run(host_set_av_sync(AVSyncPayload(offset_ms=999), Request(scope_post)))
        self.assertEqual(res_clamp["offset_ms"], 500)

    def test_notify_on_deck_endpoint(self):
        from app import api_notify_on_deck
        from database import save_track, enqueue_song, get_or_create_singer
        import asyncio

        singer = get_or_create_singer("token_ondeck_test", "OnDeckSinger", "🍹")
        track_id = save_track("v_ondeck_1", "OnDeck Song", "OnDeck Artist", "OnDeck Artist - OnDeck Song")
        qid = enqueue_song(track_id, singer["id"])

        res = asyncio.run(api_notify_on_deck(qid))
        self.assertEqual(res["status"], "ok")

    def test_system_health_endpoint(self):
        from app import api_get_state, api_host_health
        from config import HOST_TOKEN
        from starlette.requests import Request
        import asyncio

        # 1. Verify system_health and network included in api_get_state
        state = asyncio.run(api_get_state())
        self.assertIn("system_health", state)
        self.assertIn("ffmpeg_installed", state["system_health"])
        self.assertIn("port", state["system_health"])
        self.assertIn("network", state)
        self.assertIn("base_url", state["network"])
        self.assertIn("remote_mode", state["network"])
        self.assertIn("is_https", state["network"])

        # 2. Verify api_host_health
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/host/health",
            "headers": [(b"cookie", f"tiki_host={HOST_TOKEN}".encode())]
        }
        health = asyncio.run(api_host_health(Request(scope)))
        self.assertEqual(health["status"], "ok")
        self.assertIn("ffmpeg_installed", health)
        self.assertIn("port", health)

if __name__ == "__main__":
    unittest.main()


