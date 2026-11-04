import tempfile
import unittest
from pathlib import Path

import numpy as np

from construction_safety.analytics import Analytics, Track
from construction_safety.dashboard import current_run, recent_events
from construction_safety.incidents import IncidentLog


class IncidentTests(unittest.TestCase):
    def test_timestamp_pid_location_and_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = IncidentLog(root, "session", "src2", 1)
            event = {
                "person_id": "W0001",
                "worker_id": "W0001",
                "type": "ppe_missing",
                "level": 2,
                "item": "vest",
                "zone": "RED",
                "worker_box": [1, 2, 20, 40],
            }
            record = log.write(event, 17, 3.21, np.zeros((60, 80, 3), np.uint8))
            log.close()
            self.assertEqual(record["pid"], "src2:session:W0001")
            self.assertEqual(record["person_ids"], ["W0001"])
            self.assertTrue(record["timestamp_utc"].endswith("+00:00"))
            self.assertTrue((root / record["snapshot"]).exists())
            self.assertEqual(recent_events(root / "events.jsonl"), [record])
            self.assertEqual(record["zone"], "RED")
            with (root / "events.jsonl").open("a") as stream:
                stream.write('{"incomplete":')
            self.assertEqual(recent_events(root / "events.jsonl"), [record])
            with self.assertRaises(FileExistsError):
                IncidentLog(root, "s", "src1", 0)

    def test_camera_cannot_select_arbitrary_path(self):
        with self.assertRaises(ValueError):
            current_run("../../etc")

    def test_red_person_highlight_is_independent_of_ppe_and_unknown_is_neutral(self):
        config = {
            "zones": [{"name": "RED", "level": 3, "points": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]}]
        }
        a = Analytics(config, 200, 200)
        w = Track("W1", (110, 50, 160, 180), 0.9, "Person", "worker", 0)
        a.update([w], 0)
        frame = a.render(np.zeros((200, 200, 3), np.uint8), [w], 5)
        self.assertGreater(int(frame[100, 130, 2]), int(frame[100, 130, 0]))
        w.box = (10, 50, 40, 180)
        a.update([w], 0.2)
        frame = a.render(np.zeros((200, 200, 3), np.uint8), [w], 5)
        self.assertEqual(tuple(frame[100, 10]), (210, 210, 210))
