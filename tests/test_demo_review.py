import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from construction_safety.incidents import IncidentLog


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = IncidentLog(
            self.root, "session", "src6", 0, {"mode": "demo_once", "collection_seconds": 10}
        )
        self.frame = np.zeros((60, 80, 3), np.uint8)
        self.event = {
            "person_id": "W1",
            "worker_id": "W1",
            "type": "ppe_missing",
            "item": "vest",
            "zone": "RED",
            "level": 2,
        }

    def tearDown(self):
        self.log.close()
        self.tmp.cleanup()

    def emit(self, t, **changes):
        return self.log.write({**self.event, **changes}, int(t), t, self.frame)

    def test_new_tracking_ids_and_repeated_observations_group(self):
        first = self.emit(1)
        second = self.emit(2, person_id="W2", worker_id="W2")
        self.assertEqual(first["incident_id"], second["incident_id"])
        rows = json.loads((self.root / "incidents.json").read_text())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["person_ids"], ["W1", "W2"])
        self.assertEqual(rows[0]["observation_count"], 2)
        self.assertEqual(len(list((self.root / "incidents").glob("*.jpg"))), 1)

    def test_different_hazards_items_and_zones_remain_distinct(self):
        self.emit(1)
        self.emit(2, item="hardhat")
        self.emit(3, zone="OPEN")
        self.emit(4, type="danger_zone_entry", item=None, level=3)
        self.assertEqual(self.log.summary(4)["incident_count"], 4)

    def test_replays_do_not_write_logs_snapshots_or_findings(self):
        self.emit(1)
        before = (self.root / "events.jsonl").read_bytes()
        for t in (10, 11, 20, 30, 100):
            self.assertIsNone(self.emit(t, person_id="W99"))
        self.assertEqual((self.root / "events.jsonl").read_bytes(), before)
        self.assertEqual(self.log.summary(100)["incident_count"], 1)
        self.assertTrue(self.log.summary(100)["complete"])
        self.assertEqual(self.log.summary(100)["replay_observations_ignored"], 5)

    def test_new_review_archives_and_rearms_without_erasing_audit(self):
        first = self.emit(1)
        old_id = self.log.review_id
        (self.root / "review-request.json").write_text(json.dumps({"token": "request-1"}))
        self.assertTrue(self.log.apply_control(30))
        self.assertTrue((self.root / "reviews" / f"{old_id}.json").exists())
        self.assertEqual(self.log.summary(30)["incident_count"], 0)
        second = self.emit(31)
        self.assertNotEqual(first["incident_id"], second["incident_id"])
        self.assertIsNone(self.log.apply_control(32))
        self.assertEqual(len((self.root / "events.jsonl").read_text().splitlines()), 2)

    def test_continuous_mode_does_not_suppress_later_events(self):
        other = self.root / "continuous"
        other.mkdir()
        with self.assertRaises(ValueError):
            IncidentLog(other, "s", "src6", 0, {"mode": "demo_once", "collection_seconds": 0})
        log = IncidentLog(other, "s", "src6", 0)
        self.assertIsNotNone(log.write(self.event, 0, 10000, self.frame))
        self.assertIsNotNone(log.write(self.event, 1, 20000, self.frame))
        log.close()
