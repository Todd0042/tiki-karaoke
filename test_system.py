import sys
import os
import unittest
from pathlib import Path

# Add current dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import (
    init_db, get_or_create_singer, get_singer_by_token,
    save_track, get_track_by_id, search_local_tracks,
    enqueue_song, get_queue, set_queue_status, remove_from_queue,
    get_singer_history, get_party_wrapped_stats, get_setting, set_setting,
    get_browsable_tracks, get_unique_downloaded_artists, get_cleanup_candidates, prune_local_track
)
from search import normalize_title_artist, extract_attributes, deduplicate_and_group, tiered_search
from downloader import sanitize_filename, normalize_audio_loudness, update_singer_m3u_playlist
from config import TRACKS_DIR, PLAYLISTS_DIR

class TestTikiKaraoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

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

if __name__ == "__main__":
    unittest.main()

