"""
Test suite for Karaoke CD / CDG (MP3+CDG and ZIP) support in Tiki Karaoke.
"""
import os
import sys
import tempfile
import zipfile
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import init_db, get_db, import_local_track, get_track_by_id, get_queue
from app import extract_cdg_zip

class TestCDGSupport(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        init_db()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def create_dummy_cdg(self, path: Path):
        # 10 valid CDG packets (24 bytes each = 240 bytes)
        # Packet 1: MemoryPreset (command 9, instruction 1)
        p1 = bytes([9, 1, 0, 0] + [0] * 16 + [0] * 4)
        # Packet 2: BorderPreset (command 9, instruction 2)
        p2 = bytes([9, 2, 0, 0] + [0] * 16 + [0] * 4)
        # 8 dummy packets
        rest = bytes([9, 6, 0, 0] + [0] * 16 + [0] * 4) * 8
        with open(path, "wb") as f:
            f.write(p1 + p2 + rest)

    def create_dummy_audio(self, path: Path):
        with open(path, "wb") as f:
            # Minimal MP3 frame header / dummy bytes
            f.write(b"\xff\xfb\x90\x64" + b"\x00" * 500)

    def test_database_cdg_columns_and_import(self):
        audio_file = Path(self.test_dir) / "Journey - Dont Stop Believin.mp3"
        cdg_file = Path(self.test_dir) / "Journey - Dont Stop Believin.cdg"
        self.create_dummy_audio(audio_file)
        self.create_dummy_cdg(cdg_file)

        tid = import_local_track(
            file_path=str(audio_file),
            title="Don't Stop Believin'",
            artist="Journey",
            duration=250,
            is_sing_along=False,
            media_type="cdg",
            cdg_path=str(cdg_file)
        )
        self.assertIsInstance(tid, int)

        track = get_track_by_id(tid)
        self.assertIsNotNone(track)
        self.assertEqual(track["media_type"], "cdg")
        self.assertEqual(track["cdg_path"], str(cdg_file.resolve()))
        self.assertEqual(track["local_path"], str(audio_file.resolve()))

    def test_extract_cdg_zip(self):
        zip_path = Path(self.test_dir) / "Queen - Bohemian Rhapsody.zip"
        temp_audio = Path(self.test_dir) / "temp.mp3"
        temp_cdg = Path(self.test_dir) / "temp.cdg"
        self.create_dummy_audio(temp_audio)
        self.create_dummy_cdg(temp_cdg)

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(temp_audio, arcname="Queen - Bohemian Rhapsody.mp3")
            zf.write(temp_cdg, arcname="Queen - Bohemian Rhapsody.cdg")

        extracted = extract_cdg_zip(zip_path)
        self.assertIsNotNone(extracted)
        extracted_audio, extracted_cdg = extracted
        self.assertTrue(os.path.exists(extracted_audio))
        self.assertTrue(os.path.exists(extracted_cdg))
        self.assertTrue(extracted_audio.endswith(".mp3"))
        self.assertTrue(extracted_cdg.endswith(".cdg"))

if __name__ == "__main__":
    unittest.main()
