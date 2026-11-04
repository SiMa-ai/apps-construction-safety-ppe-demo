import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from construction_safety.dashboard import shared_json
from construction_safety.source_settings import SourceSettings
from construction_safety.video_relay import read_exact


class DeviceIntegrationTests(unittest.TestCase):
    def test_summary_cache_preserves_timestamp_and_does_not_cross_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "summary.json"
            original = {"updated_at": "2026-01-01T00:00:00+00:00", "frames": 4}
            path.write_text(json.dumps(original))
            self.assertEqual(shared_json(path), original)
            path.unlink()
            with patch("construction_safety.dashboard.time.sleep"):
                self.assertEqual(shared_json(path), original)
                with self.assertRaises(OSError):
                    shared_json(Path(folder) / "different-run.json")

    def test_relay_truncated_packet_fails(self):
        self.assertEqual(read_exact(io.BytesIO(b"abc"), 3), b"abc")
        with self.assertRaises(EOFError):
            read_exact(io.BytesIO(b"ab"), 3)

    def test_device_lifecycle_uses_remote_helper_and_saved_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "configs").mkdir()
            (root / "configs/dashboard.json").write_text(
                json.dumps(
                    {
                        "runtime": {
                            "backend": "neat-device",
                            "python": "/home/sima/pyneat/bin/python",
                            "ssh_target": "sima@device",
                        }
                    }
                )
            )
            manager = SourceSettings(root, ("src1",), threading.Lock())
            with patch(
                "construction_safety.source_settings.subprocess.run",
                return_value=Mock(stdout='{"alive": true}'),
            ) as run:
                self.assertTrue(manager._process_alive("src1"))
                command = run.call_args.args[0]
                self.assertEqual(command[0], "ssh")
                self.assertIn("sima@device", command)
                self.assertIn("device_camera.py", command[-1])
                self.assertIn("status src1", command[-1])

    def test_relay_preview_avoids_shared_files_and_keeps_original_time(self):
        from types import SimpleNamespace

        from construction_safety.dashboard import small_preview

        frame = (b"\xff\xd8jpeg", '"123"', 123.0)
        relay = SimpleNamespace(preview_lock=threading.Lock(), previews={"src1": frame})
        with (
            patch("construction_safety.dashboard.video_relay", relay),
            patch("construction_safety.dashboard._small_preview") as fallback,
        ):
            self.assertEqual(small_preview("src1"), frame)
            fallback.assert_not_called()
