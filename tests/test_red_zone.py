import unittest

from construction_safety.analytics import Analytics, Detection, Track, load_zones
from construction_safety.red_zone import associate_ppe, zone_for_box


def worker(ident="W1", box=(0, 0, 100, 200)):
    return Track(ident, box, 0.9, "Person", "worker", 0)


class MembershipTests(unittest.TestCase):
    def setUp(self):
        self.zones = load_zones(
            {
                "zones": [
                    {
                        "name": "RED",
                        "level": 3,
                        "restricted": True,
                        "points": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]],
                    }
                ]
            },
            100,
            100,
        )

    def test_foot_corner_detects_partial_entry_missed_by_midpoint(self):
        box = (30, 10, 60, 90)
        self.assertIsNone(zone_for_box(box, self.zones, "bottom_center"))
        self.assertEqual(zone_for_box(box, self.zones)["name"], "RED")

    def test_boundary_policy_is_explicit(self):
        box = (20, 10, 50, 90)
        self.assertIsNotNone(zone_for_box(box, self.zones, include_boundary=True))
        self.assertIsNone(zone_for_box(box, self.zones, include_boundary=False))

    def test_two_feet_and_overlapping_zones_count_one_person(self):
        cfg = {
            "zones": [
                {
                    "name": "R",
                    "level": 3,
                    "restricted": True,
                    "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                }
            ],
            "proximity": {"danger": 0.1, "warning": 0.2, "caution": 0.3},
            "zone_membership": {"mode": "either_bottom_corner", "include_boundary": True},
        }
        a = Analytics(cfg, 200, 300)
        a.update([worker(box=(10, 10, 110, 210))], 0)
        self.assertEqual(a.occupancy["workers_total"], 1)
        self.assertEqual(a.occupancy["red_zone_in"], 1)
        self.assertEqual(a.occupancy["red_zone_out"], 0)
        a.update([], 1)
        self.assertEqual(a.occupancy["red_zone_in"], 0)
        self.assertEqual(a.red_peak, 1)


class PPETests(unittest.TestCase):
    def test_absence_is_unknown_not_missing(self):
        self.assertEqual(
            associate_ppe([worker()], [])["W1"], {"hardhat": "unknown", "vest": "unknown"}
        )

    def test_explicit_negative_and_positive_evidence(self):
        detections = [
            Detection((20, 5, 60, 40), 0.9, "NO-Hardhat", "ppe"),
            Detection((15, 70, 85, 140), 0.9, "Safety Vest", "ppe"),
        ]
        state = associate_ppe([worker()], detections)["W1"]
        self.assertEqual(state, {"hardhat": "missing", "vest": "present"})

    def test_conflicting_evidence_is_not_compliance(self):
        ds = [Detection((20, 5, 60, 40), 0.9, label, "ppe") for label in ["Hardhat", "NO-Hardhat"]]
        self.assertEqual(associate_ppe([worker()], ds)["W1"]["hardhat"], "conflict")

    def test_ambiguous_overlapping_workers_do_not_share_one_helmet(self):
        ds = [Detection((20, 5, 60, 40), 0.9, "Hardhat", "ppe")]
        state = associate_ppe([worker(), worker("W2")], ds)
        self.assertTrue(all(s["hardhat"] == "unknown" for s in state.values()))

    def test_helmet_near_feet_does_not_count(self):
        ds = [Detection((20, 160, 60, 195), 0.9, "Hardhat", "ppe")]
        self.assertEqual(associate_ppe([worker()], ds)["W1"]["hardhat"], "unknown")


class GroundContactTests(unittest.TestCase):
    def setUp(self):
        from construction_safety.red_zone import ground_contact
        self.assess = ground_contact
        self.zones = load_zones({"zones": [{
            "name": "RED", "level": 3, "restricted": True,
            "points": [[0.5, 0.1], [0.9, 0.1], [0.9, 0.9], [0.5, 0.9]],
        }]}, 100, 100)

    def test_corner_overlap_does_not_put_ground_center_inside(self):
        zone, evidence = self.assess((20, 10, 60, 70), self.zones, 100, 100)
        self.assertIsNone(zone)
        self.assertEqual(evidence["state"], "outside")

    def test_boundary_is_uncertain_not_safe_or_inside(self):
        zone, evidence = self.assess((40, 10, 60, 70), self.zones, 100, 100)
        self.assertIsNone(zone)
        self.assertEqual(evidence["state"], "uncertain")
        self.assertEqual(evidence["zone"], "RED")

    def test_truncated_feet_are_unknown(self):
        zone, evidence = self.assess((60, 10, 80, 99), self.zones, 100, 100)
        self.assertIsNone(zone)
        self.assertEqual(evidence["reason"], "feet_outside_frame")

    def test_clear_ground_entry_remains_inside(self):
        zone, evidence = self.assess((60, 10, 80, 70), self.zones, 100, 100)
        self.assertEqual(zone["name"], "RED")
        self.assertFalse(evidence["depth_measured"])

    def test_interrupted_entry_must_restart_dwell(self):
        from construction_safety.analytics import Gate
        gate = Gate(hold_s=0.3, release_s=1)
        observed = {"worker": (3, {"type": "danger_zone_entry"})}
        gate.update(observed, 0)
        gate.update({}, 0.2)
        self.assertEqual(gate.update(observed, 0.4), [])
        self.assertEqual(len(gate.update(observed, 0.8)), 1)

    def test_uncertain_worker_not_counted_as_outside_or_logged_as_entry(self):
        config = {
            "zones": [{"name": "RED", "level": 3, "restricted": True,
                       "points": [[0.5, 0.1], [0.9, 0.1], [0.9, 0.9], [0.5, 0.9]]}],
            "zone_membership": {"mode": "ground_contact"},
        }
        analytics = Analytics(config, 100, 100)
        w = worker(box=(40, 10, 60, 70))
        analytics.update([w], 0)
        self.assertEqual(analytics.update([w], 1), [])
        self.assertEqual(analytics.occupancy["zone_uncertain"], 1)
        self.assertEqual(analytics.occupancy["red_zone_out"], 0)
        self.assertEqual(analytics.occupancy["red_zone_in"], 0)
