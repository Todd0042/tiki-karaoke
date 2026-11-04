import unittest
import os
import shutil
import tempfile
from pathlib import Path

# Set test environment
os.environ["TIKI_TESTING"] = "1"
os.environ["TIKI_DATA_DIR"] = tempfile.mkdtemp(prefix="tiki_cache_test_")

from database import init_db, save_track, update_track_download_status
from services.library_cache import (
    warm_library_cache,
    invalidate_library_cache,
    get_cached_unique_artists,
    search_cached_tracks
)
from search import get_search_ydl_opts, _YOUTUBE_SEARCH_CACHE


class TestLibraryCacheAndSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        # Seed test tracks
        t1 = save_track(video_id="test_cache_1", title="Bohemian Rhapsody", artist="Queen", original_title="Queen - Bohemian Rhapsody")
        update_track_download_status(t1, "ready", local_path="/tmp/fake_bohemian.mp4")

        t2 = save_track(video_id="test_cache_2", title="Under Pressure", artist="Queen", original_title="Queen - Under Pressure")
        update_track_download_status(t2, "ready", local_path="/tmp/fake_under_pressure.mp4")

        t3 = save_track(video_id="test_cache_3", title="Party Up", artist="DMX", original_title="DMX - Party Up")
        update_track_download_status(t3, "ready", local_path="/tmp/fake_party_up.mp4")

    @classmethod
    def tearDownClass(cls):
        data_dir = os.environ.get("TIKI_DATA_DIR")
        if data_dir and Path(data_dir).exists():
            shutil.rmtree(data_dir, ignore_errors=True)

    def test_library_cache_warming_and_search(self):
        warm_library_cache(force=True)
        artists = get_cached_unique_artists(sing_along=False)
        self.assertIn("Queen", artists)
        self.assertIn("DMX", artists)

        # In-memory search
        queen_results = search_cached_tracks("queen")
        self.assertEqual(len(queen_results), 2)
        titles = [r["title"] for r in queen_results]
        self.assertIn("Bohemian Rhapsody", titles)
        self.assertIn("Under Pressure", titles)

        # Invalidation test
        invalidate_library_cache()
        artists_after_invalidation = get_cached_unique_artists(sing_along=False)
        self.assertIn("Queen", artists_after_invalidation)

    def test_search_ydl_opts_enforces_ipv4(self):
        opts = get_search_ydl_opts(timeout=3)
        self.assertEqual(opts.get("source_address"), "0.0.0.0")
        self.assertTrue(opts.get("extract_flat"))
        self.assertEqual(opts.get("socket_timeout"), 3)


if __name__ == "__main__":
    unittest.main()
