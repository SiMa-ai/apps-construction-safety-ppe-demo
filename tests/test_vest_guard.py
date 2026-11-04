import unittest

import numpy as np

from construction_safety.analytics import Analytics, Detection, Track
from construction_safety.red_zone import guard_missing_vests


class VestGuardTests(unittest.TestCase):
    def setUp(self):
        self.worker = Track("W1", (20, 0, 120, 200), 0.9, "person", "worker", 0)
        self.config = {"enabled": True}
        self.states = {"W1": {"vest": "missing", "hardhat": "missing"}}
        self.frame = np.zeros((220, 160, 3), np.uint8)

    def test_neon_torso_conflicts_with_negative_without_certifying_or_changing_helmet(self):
        self.frame[40:110, 40:100] = (0, 255, 220)
        notes = guard_missing_vests(self.frame, [self.worker], self.states, self.config)
        self.assertEqual(self.states["W1"], {"vest": "conflict", "hardhat": "missing"})
        self.assertIn("vest", notes["W1"])

    def test_bright_pants_or_background_do_not_suppress_missing_vest(self):
        self.frame[150:200, 20:120] = (0, 255, 220)
        self.frame[:, 125:] = (0, 255, 220)
        self.assertEqual(
            guard_missing_vests(self.frame, [self.worker], self.states, self.config), {}
        )
        self.assertEqual(self.states["W1"]["vest"], "missing")

    def test_neighbour_vest_is_not_borrowed(self):
        self.frame[40:110, 40:100] = (0, 255, 220)
        other = Track("W2", (35, 30, 105, 180), 0.9, "person", "worker", 0)
        guard_missing_vests(self.frame, [self.worker, other], self.states, self.config)
        self.assertEqual(self.states["W1"]["vest"], "missing")

    def test_guard_is_opt_in_and_positive_detection_is_unchanged(self):
        self.frame[40:110, 40:100] = (0, 255, 220)
        guard_missing_vests(self.frame, [self.worker], self.states, {})
        self.assertEqual(self.states["W1"]["vest"], "missing")
        self.states["W1"]["vest"] = "present"
        guard_missing_vests(self.frame, [self.worker], self.states, self.config)
        self.assertEqual(self.states["W1"]["vest"], "present")

    def test_suppressed_vest_keeps_independent_helmet_and_danger_incidents(self):
        cfg = {
            "ppe": {
                "enabled": True,
                "log_missing": True,
                "hold_s": 0.1,
                "min_observations": 2,
                "high_visibility_vest_guard": self.config,
            },
            "event_hold_s": 0.1,
            "zones": [{"name": "RED", "level": 3, "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
        }
        a = Analytics(cfg, 160, 220)
        self.worker.box = (20, 10, 120, 200)
        self.frame[40:110, 40:100] = (0, 255, 220)
        ds = [
            Detection((40, 40, 100, 110), 0.9, "NO-Safety Vest", "ppe"),
            Detection((40, 2, 100, 30), 0.9, "NO-Hardhat", "ppe"),
        ]
        a.update([self.worker], 0, ds, frame=self.frame)
        events = a.update([self.worker], 0.2, ds, frame=self.frame)
        self.assertEqual(
            [(e["type"], e.get("item")) for e in events],
            [("danger_zone_entry", None), ("ppe_missing", "hardhat")],
        )
