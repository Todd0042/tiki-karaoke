"""
Hermetic Unit Test Suite for Tiki Karaoke Vibe Engine & Audio Router.
Designed for 100% CI compatibility in headless, hardware-free environments (GitHub Actions).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Configure isolated environment before imports
_test_dir = tempfile.mkdtemp(prefix="tiki_vibe_test_")
os.environ["TIKI_DATA_DIR"] = _test_dir
os.environ["TIKI_TESTING"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import (
    init_db,
    get_db,
    set_setting,
    get_next_vibe_filler_track,
    record_filler_play,
)
from audio_router import (
    NativeAudioRouter,
    MONITORING_MODES,
    DEFAULT_MONITORING_MODE,
)


class TestVibeEngineDeduplicationAndScoring(unittest.TestCase):
    """Verifies Vibe Engine FIFO ring-buffer deduplication and fair rotation scoring."""

    @classmethod
    def setUpClass(cls):
        os.environ["TIKI_TESTING"] = "1"
        init_db()
        set_setting("strict_byom_only", "false")

    def setUp(self):
        conn = get_db()
        try:
            conn.execute("DELETE FROM filler_play_history")
            conn.execute("DELETE FROM filler_tracks")
            conn.execute("DELETE FROM tracks")
            conn.execute("DELETE FROM history")
            conn.commit()
        finally:
            conn.close()

    def _insert_filler(self, title: str, artist: str, genre: str = "Pop", year: int = 2000, play_count: int = 0) -> int:
        conn = get_db()
        try:
            cur = conn.execute("""
                INSERT INTO filler_tracks (video_id, title, artist, original_title, genre, release_year, download_status, is_downloaded, play_count)
                VALUES (?, ?, ?, ?, ?, ?, 'ready', 1, ?)
            """, (f"vid_{title.lower().replace(' ', '_')}", title, artist, title, genre, year, play_count))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _insert_sing_along(self, title: str, artist: str, genre: str = "Rock", year: int = 1995, play_count: int = 0) -> int:
        conn = get_db()
        try:
            cur = conn.execute("""
                INSERT INTO tracks (video_id, title, artist, original_title, genre, release_year, download_status, is_downloaded, is_sing_along, play_count)
                VALUES (?, ?, ?, ?, ?, ?, 'ready', 1, 1, ?)
            """, (f"sing_{title.lower().replace(' ', '_')}", title, artist, title, genre, year, play_count))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def test_fair_rotation_prioritizes_lowest_play_count(self):
        """Verifies that candidate tracks with lower play counts are selected first."""
        id_high = self._insert_filler("Overplayed Anthem", "Band A", play_count=10)
        id_med = self._insert_filler("Moderate Jam", "Band B", play_count=3)
        id_fresh = self._insert_filler("Fresh Track", "Band C", play_count=0)

        selected = get_next_vibe_filler_track(mode="pure_filler")
        self.assertIsNotNone(selected)
        self.assertEqual(selected["id"], id_fresh, "Track with play_count=0 must be prioritized over played tracks")

    def test_fifo_blacklist_ring_buffer_excludes_recent_plays(self):
        """Verifies the 6-play FIFO ring buffer strictly excludes recently played filler tracks."""
        # Insert 8 distinct filler tracks
        ids = [self._insert_filler(f"Track {i}", f"Artist {i}", play_count=0) for i in range(1, 9)]

        # Simulate playing tracks 1 through 5
        for fid in ids[:5]:
            record_filler_play(fid, is_sing_along=False)

        # The next selected track should be from the unplayed candidates (6, 7, or 8), not 1-5
        selected = get_next_vibe_filler_track(mode="pure_filler")
        self.assertIsNotNone(selected)
        self.assertIn(
            selected["id"],
            ids[5:],
            f"Expected track from unplayed set {ids[5:]}, got {selected['id']}"
        )

    def test_adaptive_ring_buffer_never_starves_small_crates(self):
        """When the crate has only 2 songs, the ring buffer adapts so playback never halts."""
        id1 = self._insert_filler("Song One", "Artist A", play_count=1)
        id2 = self._insert_filler("Song Two", "Artist B", play_count=1)

        # Record Song One as played
        record_filler_play(id1, is_sing_along=False)

        # Should select Song Two
        sel1 = get_next_vibe_filler_track(mode="pure_filler")
        self.assertIsNotNone(sel1)
        self.assertEqual(sel1["id"], id2)

        # Record Song Two as played
        record_filler_play(id2, is_sing_along=False)

        # With only 2 tracks, buffer adapts and still returns a valid track without starving
        sel2 = get_next_vibe_filler_track(mode="pure_filler")
        self.assertIsNotNone(sel2)
        self.assertIn(sel2["id"], [id1, id2])

    def test_mode_selection_and_pause_state(self):
        """Verifies pure_filler, sing_along, and intelligent_mashup mode selection behavior."""
        f_id = self._insert_filler("Filler BGM", "Filler Artist")
        s_id = self._insert_sing_along("SingAlong Track", "Sing Along Artist")

        # In pure_filler mode, filler track is chosen
        res_filler = get_next_vibe_filler_track(mode="pure_filler")
        self.assertIsNotNone(res_filler)
        self.assertEqual(res_filler["track_type"], "filler")

        # In sing_along mode, sing-along track is chosen
        res_sing = get_next_vibe_filler_track(mode="sing_along")
        self.assertIsNotNone(res_sing)
        self.assertEqual(res_sing["track_type"], "sing_along")

        # In intelligent_mashup when paused -> filler music video
        res_paused = get_next_vibe_filler_track(mode="intelligent_mashup", is_paused=True)
        self.assertIsNotNone(res_paused)
        self.assertEqual(res_paused["track_type"], "filler")

        # In intelligent_mashup when running empty queue -> sing-along
        res_unpaused = get_next_vibe_filler_track(mode="intelligent_mashup", is_paused=False)
        self.assertIsNotNone(res_unpaused)
        self.assertEqual(res_unpaused["track_type"], "sing_along")

    def test_recent_sung_song_cooldown_blocks_duplicate_performance(self):
        """Verifies tracks sung by live guests within 90 minutes are suppressed from filler rotation."""
        conn = get_db()
        try:
            s_cur = conn.execute("INSERT INTO singers (name, token) VALUES ('Alice', 'token_alice_123')")
            singer_id = s_cur.lastrowid
            cur = conn.execute("""
                INSERT INTO tracks (video_id, title, artist, original_title, download_status, is_downloaded, is_sing_along, play_count)
                VALUES ('live_song_1', 'Bohemian Rhapsody', 'Queen', 'Bohemian Rhapsody', 'ready', 1, 0, 1)
            """)
            live_track_id = cur.lastrowid
            conn.execute("""
                INSERT INTO history (track_id, singer_id, played_at)
                VALUES (?, ?, datetime('now', '-15 minutes'))
            """, (live_track_id, singer_id))
            conn.commit()
        finally:
            conn.close()

        # Insert a filler track with the exact same song
        filler_queen = self._insert_filler("Bohemian Rhapsody", "Queen", play_count=0)
        filler_other = self._insert_filler("Don't Stop Me Now", "Queen", play_count=0)

        # Bohemian Rhapsody should be cooled down, selecting the other track instead
        selected = get_next_vibe_filler_track(mode="pure_filler")
        self.assertIsNotNone(selected)
        self.assertEqual(selected["id"], filler_other, "Live sung song must be cooled down from filler rotation")


class TestAudioRouterModeSwitching(unittest.TestCase):
    """Verifies AudioRouter state machine, volume clamping, and monitoring mode transitions without hardware dependencies."""

    def setUp(self):
        self.router = NativeAudioRouter()

    def test_default_monitoring_mode(self):
        """Verifies default monitoring mode is initialized to 'native'."""
        self.assertEqual(self.router.monitoring_mode, DEFAULT_MONITORING_MODE)
        self.assertIn(self.router.monitoring_mode, MONITORING_MODES)

    def test_monitoring_mode_transitions(self):
        """Verifies transitions between native, hardware_direct, and browser monitoring modes."""
        with patch.object(self.router, "stop_loopback") as mock_stop:
            # Transition to hardware_direct stops native loopback
            self.router.set_monitoring_mode("hardware_direct")
            self.assertEqual(self.router.monitoring_mode, "hardware_direct")
            mock_stop.assert_called_once()

        with patch.object(self.router, "stop_loopback") as mock_stop:
            # Transition to browser stops loopback
            self.router.set_monitoring_mode("browser")
            self.assertEqual(self.router.monitoring_mode, "browser")
            mock_stop.assert_called_once()

        with patch.object(self.router, "stop_loopback") as mock_stop:
            # Transition back to native doesn't call stop_loopback
            self.router.set_monitoring_mode("native")
            self.assertEqual(self.router.monitoring_mode, "native")
            mock_stop.assert_not_called()

    def test_unknown_monitoring_mode_is_ignored(self):
        """Verifies invalid or unknown mode strings do not alter router state."""
        self.router.set_monitoring_mode("nonexistent_mode")
        self.assertEqual(self.router.monitoring_mode, DEFAULT_MONITORING_MODE)

    def test_volume_clamping_boundaries(self):
        """Verifies volume is strictly clamped between 0% and 150%."""
        with patch.object(self.router, "is_available", return_value=False):
            self.router.set_volume(-50)
            self.assertEqual(self.router.volume_pct, 0)

            self.router.set_volume(250)
            self.assertEqual(self.router.volume_pct, 150)

            self.router.set_volume(85)
            self.assertEqual(self.router.volume_pct, 85)

    def test_mute_state_toggling(self):
        """Verifies mute boolean state toggling."""
        with patch.object(self.router, "is_available", return_value=False):
            self.router.set_muted(True)
            self.assertTrue(self.router.is_muted)

            self.router.set_muted(False)
            self.assertFalse(self.router.is_muted)

    def test_get_state_structure(self):
        """Verifies get_state returns expected contract for host controls."""
        state = self.router.get_state()
        self.assertIn("available", state)
        self.assertIn("active", state)
        self.assertIn("muted", state)
        self.assertIn("volume", state)
        self.assertIn("device", state)
        self.assertIn("monitoring_mode", state)
        self.assertEqual(state["monitoring_mode"], self.router.monitoring_mode)


if __name__ == "__main__":
    unittest.main()
