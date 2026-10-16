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
    get_singer_history, get_party_wrapped_stats, get_setting, set_setting
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
        s1 = get_or_create_singer("s1", "Alice", "🌺")
        s2 = get_or_create_singer("s2", "Bob", "🌴")

        t1 = save_track("v_a", "Song A", "Artist A", "Artist A - Song A")
        t2 = save_track("v_b", "Song B", "Artist B", "Artist B - Song B")

        # Alice queues Song A
        q1 = enqueue_song(t1, s1["id"], pitch_shift=-2)
        # Bob queues Song B
        q2 = enqueue_song(t2, s2["id"], pitch_shift=1)

        queue = get_queue()
        self.assertGreaterEqual(len(queue), 2)
        self.assertEqual(queue[0]["pitch_shift"], -2)

        # Set status to playing then completed
        set_queue_status(q1, "playing")
        set_queue_status(q1, "completed")

        history = get_singer_history(s1["id"])
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["title"], "Song A")

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

if __name__ == "__main__":
    unittest.main()
