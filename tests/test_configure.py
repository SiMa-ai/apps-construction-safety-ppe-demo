import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.control_camera import ROOT, configure


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / "configs").mkdir()
        for name in ("dashboard.example.json", "camera.example.json"):
            shutil.copy2(ROOT / "configs" / name, self.root / "configs" / name)

    def run_wizard(self, force=False):
        answers = ["", "device.example.com", "", "sdk.example.com", "", "", "", "", ""]
        with (
            patch("builtins.input", side_effect=answers),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            configure(self.root, force)

    def test_generates_local_routes_and_empty_zones(self):
        self.run_wizard()
        config = json.loads((self.root / "configs/dashboard.json").read_text())
        self.assertEqual(config["runtime"]["ssh_target"], "sima@device.example.com")
        self.assertEqual(config["runtime"]["video_port_base"], 29000)
        for index in (1, 2):
            camera = json.loads((self.root / f"configs/src{index}.json").read_text())
            self.assertEqual(camera["zones"], [])
            self.assertEqual(camera["input_media"]["source"], index)
            self.assertEqual(camera["input_media"]["video"], "")

    def test_refuses_overwrite_and_force_preserves_backup(self):
        self.run_wizard()
        original = (self.root / "configs/src1.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            configure(self.root)
        self.run_wizard(force=True)
        backups = list((self.root / "runs").glob("config-backup-*/src1.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
