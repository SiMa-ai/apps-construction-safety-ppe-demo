import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from construction_safety.source_settings import SourceSettings
from construction_safety.zone_settings import revision


class SourceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "configs").mkdir()
        (self.root / "runs").mkdir()
        (self.root / "configs/dashboard.json").write_text(json.dumps({"cameras": ["src6", "src7"]}))
        for name in ("src6", "src7"):
            (self.root / f"configs/{name}.json").write_text(
                json.dumps(
                    {
                        "model": "yolo.pt",
                        "zones": [{"name": "old", "level": 3, "points": [[0, 0], [1, 0], [1, 1]]}],
                    }
                )
            )
        self.manager = SourceSettings(self.root, ("src6", "src7"), threading.Lock())
        self.sources = [
            {"index": n, "file": v, "state": "playing" if v else "stopped", "transport": "rtsp"}
            for n, v in ((6, "a.mp4"), (7, "b.mp4"), (8, ""))
        ]
        self.calls = []

        def api(path, payload=None):
            self.calls.append((path, payload))
            if path == "mediasrc":
                return self.sources
            if path == "mediasrc/videos":
                return ["a.mp4", "b.mp4", "c.mp4"]
            if payload:
                source = next(s for s in self.sources if s["index"] == payload["index"])
                if path.endswith("/assign"):
                    source["file"] = payload["file"]
                if path.endswith("/start"):
                    source["state"] = "playing"
                if path.endswith("/stop"):
                    source["state"] = "stopped"
            return {}

        self.patcher = patch("construction_safety.source_settings.insight", side_effect=api)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.manager._stop = lambda n: self.calls.append(("stop_pipeline", n))
        self.manager._start = lambda n, r: self.calls.append(("start_pipeline", (n, dict(r))))
        self.manager._process_alive = lambda n: True

    def request(self, **extra):
        return {
            "camera": "src6",
            "action": "apply",
            "source": 6,
            "channel": 0,
            "video": "a.mp4",
            "revision": revision(self.manager.mappings()),
            **extra,
        }

    def finish(self):
        self.assertTrue(self.manager.lock.acquire(timeout=3))
        self.manager.lock.release()

    def test_rejections_do_not_mutate(self):
        for extra in (
            {"channel": 80},
            {"source": 7},
            {"source": True},
            {"video": "../bad"},
            {"video": "c.mp4"},
            {"revision": "old"},
            {"camera": "other"},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.manager.submit(self.request(**extra))
        self.assertFalse(
            any(p.startswith("mediasrc/") and p != "mediasrc/videos" for p, _ in self.calls)
        )
        self.assertFalse(self.manager.lock.locked())

    def test_swap_updates_both_outputs_and_audits(self):
        self.manager.submit(self.request(channel=1))
        self.finish()
        self.assertEqual(self.manager.job["status"], "done")
        self.assertEqual(self.manager.mappings()["src7"]["channel"], 0)
        self.assertIn(
            ("start_pipeline", ("src6", {"source": 6, "channel": 1, "video": "a.mp4"})), self.calls
        )
        self.assertIn(("start_pipeline", ("src7", {"source": 7, "channel": 0})), self.calls)
        log = json.loads((self.root / "runs/camera-settings.jsonl").read_text())
        self.assertEqual(log["before"]["src6"]["channel"], 0)
        self.assertEqual(log["after"]["src6"]["channel"], 1)

    def test_new_video_backs_up_zones_and_records_provenance(self):
        self.manager.submit(self.request(source=8, video="c.mp4", reset_zones=True))
        self.finish()
        self.assertEqual(self.manager.job["status"], "done")
        config = json.loads((self.root / "configs/src6.json").read_text())
        self.assertEqual(config["zones"], [])
        self.assertEqual(config["model"], "yolo.pt")
        self.assertEqual(config["input_media"]["video"], "c.mp4")
        self.assertEqual(len(list((self.root / "configs/zone-history").glob("*.json"))), 1)
        self.assertIn(("mediasrc/stop", {"index": 6}), self.calls)

    def test_stop_does_not_reassign_or_clear_zones(self):
        self.manager.submit(self.request(action="stop"))
        self.finish()
        self.assertIn(("mediasrc/stop", {"index": 6}), self.calls)
        self.assertFalse(any(p == "mediasrc/assign" for p, _ in self.calls))
        self.assertTrue(json.loads((self.root / "configs/src6.json").read_text())["zones"])

    def test_busy_rejected_and_failure_reported(self):
        self.manager.lock.acquire()
        with self.assertRaises(ValueError):
            self.manager.submit(self.request())
        self.manager.lock.release()

        def fail(*args):
            raise ValueError("model failed")

        self.manager._start = fail
        self.manager.submit(self.request())
        self.finish()
        self.assertEqual(self.manager.job["status"], "error")
        self.assertIn("model failed", self.manager.job["message"])
        self.assertTrue((self.root / "runs/camera-settings.jsonl").exists())

    def test_applying_playing_source_does_not_start_it_again(self):
        self.manager.submit(self.request())
        self.finish()
        self.assertEqual(self.manager.job["status"], "done")
        self.assertFalse(any(path == "mediasrc/start" for path, _ in self.calls))
