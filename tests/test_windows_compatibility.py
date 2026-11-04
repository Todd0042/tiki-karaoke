import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from routes.launcher import (
    find_rehearsal_lounge_executable,
    find_importer_executable,
    _find_windows_registry_app,
)
import network_helper
import downloader
import services.playback as playback_service
import importer.tiki_importer.engine as importer_engine
import routes.host_controls as host_controls_route
import tray


class TestWindowsCompatibility(unittest.TestCase):
    """
    Validates Windows-specific features:
    1. Silent subprocess execution (suppressing console command prompt flash).
    2. Tiki Rehearsal Lounge discovery across Inno Setup default install paths and Registry.
    """

    @classmethod
    def setUpClass(cls):
        cls._orig_shutil_winapi = getattr(shutil, "_winapi", None)
        mock_winapi = MagicMock()
        mock_winapi.NeedCurrentDirectoryForExePath.return_value = False
        shutil._winapi = mock_winapi

    @classmethod
    def tearDownClass(cls):
        shutil._winapi = cls._orig_shutil_winapi

    def test_rehearsal_lounge_windows_program_files_discovery(self):
        """Verifies Tiki Rehearsal Lounge is located in Program Files default install path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            fake_prog_files = tmp_path / "Program Files"
            fake_rehearsal_dir = fake_prog_files / "Tiki Rehearsal Lounge"
            fake_rehearsal_dir.mkdir(parents=True)
            fake_exe = fake_rehearsal_dir / "TikiRehearsalLounge.exe"
            fake_exe.touch()

            with patch("sys.platform", "win32"), \
                 patch.dict(os.environ, {"ProgramFiles": str(fake_prog_files)}), \
                 patch("routes.launcher._find_windows_registry_app", return_value=None):
                result = find_rehearsal_lounge_executable()
                self.assertIsNotNone(result)
                cmd, work_dir = result
                self.assertEqual(cmd, [str(fake_exe)])
                self.assertEqual(work_dir, fake_rehearsal_dir)

    def test_rehearsal_lounge_windows_sibling_executable_discovery(self):
        """Verifies Tiki Rehearsal Lounge is located as a sibling install of Tiki Karaoke in Program Files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            fake_prog_files = tmp_path / "Program Files"
            fake_karaoke_dir = fake_prog_files / "Tiki Karaoke Lounge"
            fake_karaoke_dir.mkdir(parents=True)
            fake_karaoke_exe = fake_karaoke_dir / "TikiKaraoke.exe"
            fake_karaoke_exe.touch()

            fake_rehearsal_dir = fake_prog_files / "Tiki Rehearsal Lounge"
            fake_rehearsal_dir.mkdir(parents=True)
            fake_rehearsal_exe = fake_rehearsal_dir / "TikiRehearsalLounge.exe"
            fake_rehearsal_exe.touch()

            with patch("sys.platform", "win32"), \
                 patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(fake_karaoke_exe)), \
                 patch.dict(os.environ, {"ProgramFiles": str(tmp_path / "NonExistent")}), \
                 patch("routes.launcher._find_windows_registry_app", return_value=None):
                result = find_rehearsal_lounge_executable()
                self.assertIsNotNone(result)
                cmd, work_dir = result
                self.assertEqual(cmd, [str(fake_rehearsal_exe)])
                self.assertEqual(work_dir, fake_rehearsal_dir)

    def test_rehearsal_lounge_windows_registry_discovery(self):
        """Verifies Tiki Rehearsal Lounge is located via Windows Inno Setup uninstall registry keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            custom_dir = tmp_path / "CustomApps" / "RehearsalStudio"
            custom_dir.mkdir(parents=True)
            custom_exe = custom_dir / "TikiRehearsalLounge.exe"
            custom_exe.touch()

            with patch("sys.platform", "win32"), \
                 patch("routes.launcher._find_windows_registry_app", return_value=custom_exe):
                result = find_rehearsal_lounge_executable()
                self.assertIsNotNone(result)
                cmd, work_dir = result
                self.assertEqual(cmd, [str(custom_exe)])
                self.assertEqual(work_dir, custom_dir)

    def test_network_helper_ipconfig_suppresses_window(self):
        """Verifies ipconfig check_output uses CREATE_NO_WINDOW on Windows."""
        captured_kwargs = {}

        def mock_check_output(cmd, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return "Ethernet adapter Ethernet:\n   IPv4 Address. . . . . . . . . . . : 192.168.1.50\n"

        # Invalidate cache
        network_helper._cached_interface_ips = []
        network_helper._last_interface_ips_check = 0.0

        with patch("sys.platform", "win32"), \
             patch("subprocess.check_output", side_effect=mock_check_output):
            ips = network_helper.get_interface_ips()
            self.assertTrue(any(ip == "192.168.1.50" for _, ip in ips))
            self.assertIn("creationflags", captured_kwargs)
            # 0x08000000 is CREATE_NO_WINDOW
            self.assertTrue(captured_kwargs["creationflags"] & 0x08000000)

    def test_downloader_loudnorm_suppresses_window(self):
        """Verifies ffmpeg loudnorm uses CREATE_NO_WINDOW on Windows."""
        captured_kwargs = {}

        def mock_run(cmd, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            mock_res = MagicMock()
            mock_res.returncode = 0
            return mock_res

        with patch("sys.platform", "win32"), \
             patch("subprocess.run", side_effect=mock_run):
            success = downloader.normalize_audio_loudness(Path("input.mp4"), Path("output.mp4"))
            self.assertTrue(success)
            self.assertIn("creationflags", captured_kwargs)
            self.assertTrue(captured_kwargs["creationflags"] & 0x08000000)

    def test_playback_ffprobe_suppresses_window(self):
        """Verifies ffprobe in playback duration probing uses CREATE_NO_WINDOW on Windows."""
        captured_kwargs = {}

        def mock_run(cmd, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            mock_res = MagicMock()
            mock_res.stdout = "185.4\n"
            return mock_res

        with patch("sys.platform", "win32"), \
             patch("shutil.which", return_value="ffprobe"), \
             patch("subprocess.run", side_effect=mock_run):
            dur = playback_service.get_local_media_duration("sample.mp4")
            self.assertEqual(dur, 185)
            self.assertIn("creationflags", captured_kwargs)
            self.assertTrue(captured_kwargs["creationflags"] & 0x08000000)

    def test_importer_engine_ffprobe_suppresses_window(self):
        """Verifies ffprobe in importer probe_duration uses CREATE_NO_WINDOW on Windows."""
        captured_kwargs = {}

        def mock_run(cmd, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            mock_res = MagicMock()
            mock_res.stdout = "240.0\n"
            return mock_res

        with patch("sys.platform", "win32"), \
             patch("shutil.which", return_value="ffprobe"), \
             patch("subprocess.run", side_effect=mock_run):
            dur = importer_engine.probe_duration("sample.mp3")
            self.assertEqual(dur, 240)
            self.assertIn("creationflags", captured_kwargs)
            self.assertTrue(captured_kwargs["creationflags"] & 0x08000000)

    def test_tray_copy_url_suppresses_window(self):
        """Verifies clip command in tray icon uses CREATE_NO_WINDOW on Windows."""
        captured_kwargs = {}

        def mock_run(cmd, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return MagicMock()

        with patch("sys.platform", "win32"), \
             patch("subprocess.run", side_effect=mock_run):
            # Emulate _copy_url call
            flags = getattr(sys.modules["subprocess"], "CREATE_NO_WINDOW", 0x08000000)
            import subprocess
            subprocess.run("clip", input=b"http://localhost:8000", check=False, creationflags=flags)
            self.assertIn("creationflags", captured_kwargs)
            self.assertTrue(captured_kwargs["creationflags"] & 0x08000000)


if __name__ == "__main__":
    unittest.main()
