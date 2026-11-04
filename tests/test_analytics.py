import unittest

from construction_safety.analytics import (
    Analytics,
    Detection,
    Gate,
    Track,
    Tracker,
    load_zones,
    zone_for,
)


def detection(box=(10, 10, 30, 60), category="worker", label="person"):
    return Detection(box, 0.9, label, category)


def config():
    return {
        "proximity": {"danger": 0.15, "warning": 0.4, "caution": 0.8},
        "event_hold_s": 0.1,
        "event_release_s": 0.3,
        "zones": [
            {
                "name": "WALKWAY",
                "level": 0,
                "restricted": False,
                "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
            }
        ],
    }


class TrackingTests(unittest.TestCase):
    def test_short_occlusion_keeps_id_but_does_not_return_stale_object(self):
        t = Tracker(min_hits=1)
        ident = t.update([detection()], 0)[0].id
        self.assertEqual(t.update([], 0.1), [])
        self.assertEqual(t.update([detection()], 0.2)[0].id, ident)
        self.assertNotEqual(t.update([detection()], 2.0)[0].id, ident)

    def test_worker_machine_association_cannot_swap_categories(self):
        t = Tracker(min_hits=1)
        initial = t.update([detection(), detection(category="machine", label="forklift")], 0)
        updated = t.update([detection(category="machine", label="forklift"), detection()], 0.1)
        self.assertEqual({x.category: x.id for x in initial}, {x.category: x.id for x in updated})
        self.assertEqual(len({x.id for x in updated}), 2)

    def test_one_detection_cannot_claim_two_tracks(self):
        t = Tracker(min_hits=1)
        t.update([detection(), detection((35, 10, 55, 60))], 0)
        self.assertEqual(len(t.update([detection((25, 10, 45, 60))], 0.1)), 1)

    def test_confirmation_rejects_one_frame_false_positive(self):
        t = Tracker(min_hits=2)
        self.assertEqual(t.update([detection()], 0), [])
        self.assertEqual(t.confirmed["worker"], set())
        self.assertEqual(len(t.update([detection()], 0.1)), 1)


class ZoneTests(unittest.TestCase):
    def test_restricted_overlap_wins_and_left_half_is_safe(self):
        c = config()
        c["zones"].append(
            {
                "name": "RED",
                "level": 3,
                "restricted": True,
                "points": [[0.6, 0.4], [1, 0.5], [1, 0.84], [0.5, 0.7]],
            }
        )
        zones = load_zones(c, 1000, 1000)
        self.assertEqual(zone_for((750, 650), zones)["name"], "RED")
        self.assertEqual(zone_for((250, 650), zones)["level"], 0)

    def test_invalid_normalized_polygon_is_rejected(self):
        c = config()
        c["zones"][0]["points"][0] = [-0.1, 0]
        with self.assertRaises(ValueError):
            load_zones(c, 100, 100)

    def test_no_path_state_and_no_machine_color_override(self):
        a = Analytics(config(), 100, 100)
        w = Track("W1", (10, 10, 30, 60), 0.9, "person", "worker", 0)
        m = Track("M1", (25, 10, 90, 80), 0.9, "forklift", "machine", 0)
        a.update([w, m], 0)
        self.assertEqual(a.update([w, m], 0.2), [])
        self.assertFalse(a.status["W1"]["danger"])
        self.assertFalse(hasattr(w, "trail"))
        self.assertFalse(hasattr(a, "heat"))

    def test_one_danger_incident_and_reentry(self):
        c = config()
        c["zones"][0].update(level=3, restricted=True)
        a = Analytics(c, 100, 100)
        w = Track("W1", (10, 10, 30, 60), 0.9, "person", "worker", 0)
        a.update([w], 0)
        events = a.update([w], 0.2)
        self.assertEqual([e["type"] for e in events], ["danger_zone_entry"])
        self.assertTrue(a.status["W1"]["danger"])
        self.assertEqual(events[0]["person_id"], "W1")
        self.assertEqual(events[0]["worker_box"], list(w.box))
        self.assertEqual(a.update([w], 0.3), [])
        a.update([], 0.7)
        self.assertEqual(a.update([w], 0.8), [])
        self.assertEqual(len(a.update([w], 1.0)), 1)

    def test_ppe_is_separate_from_danger_and_sampling_does_not_invent_evidence(self):
        c = config()
        c["zones"][0].update(level=3, restricted=True)
        c["ppe"] = {"enabled": True, "log_missing": True, "hold_s": 0.1, "min_observations": 2}
        a = Analytics(c, 100, 100)
        w = Track("W1", (10, 10, 30, 60), 0.9, "person", "worker", 0)
        ppe = [Detection((12, 12, 25, 20), 0.9, "NO-Hardhat", "ppe")]
        a.update([w], 0, ppe)
        events = a.update([w], 0.2, ppe_updated=False)
        self.assertEqual([e["type"] for e in events], ["danger_zone_entry"])
        events = a.update([w], 0.4, ppe)
        self.assertEqual([e["type"] for e in events], ["ppe_missing"])
        self.assertEqual(events[0]["zone"], "WALKWAY")
        self.assertEqual(a.update([w], 0.6, ppe), [])
        self.assertEqual(a.status["W1"]["missing_ppe"], ["hardhat"])
        a.update([w], 3, ppe_updated=False)
        self.assertEqual(a.status["W1"]["missing_ppe"], [])

    def test_missing_ppe_outside_red_zone_and_unknown_no_alert(self):
        c = config()
        c["ppe"] = {"enabled": True, "log_missing": True, "hold_s": 0.1, "min_observations": 2}
        a = Analytics(c, 100, 100)
        w = Track("W1", (10, 10, 30, 60), 0.9, "person", "worker", 0)
        a.update([w], 0, [])
        self.assertEqual(a.update([w], 0.2, []), [])
        ppe = [Detection((12, 12, 25, 20), 0.9, "NO-Hardhat", "ppe")]
        a.update([w], 0.4, ppe)
        events = a.update([w], 0.6, ppe)
        self.assertEqual([e["type"] for e in events], ["ppe_missing"])
        self.assertFalse(a.status["W1"]["danger"])


class EventTests(unittest.TestCase):
    def test_dwell_escalation_dedup_and_reentry(self):
        g = Gate(hold_s=0.1, release_s=0.3)

        def seen(level, t):
            return g.update({"pair": (level, {"type": "machine_proximity"})}, t)

        self.assertEqual(seen(1, 0), [])
        self.assertEqual(len(seen(1, 0.11)), 1)
        self.assertEqual(seen(1, 0.2), [])
        self.assertEqual(seen(3, 0.3), [])
        self.assertEqual(seen(3, 0.41)[0]["level"], 3)
        self.assertEqual(seen(2, 0.5), [])
        g.update({}, 0.9)
        self.assertEqual(seen(3, 1.0), [])
        self.assertEqual(len(seen(3, 1.11)), 1)

    def test_slow_continuous_observations_can_confirm_alert(self):
        g = Gate(hold_s=0.3, release_s=0.6)
        self.assertEqual(g.update({"p": (3, {"type": "machine_proximity"})}, 0), [])
        self.assertEqual(len(g.update({"p": (3, {"type": "machine_proximity"})}, 2)), 1)


if __name__ == "__main__":
    unittest.main()


class RecoveryTests(unittest.TestCase):
    def test_stationary_worker_recovers_after_prediction_drift(self):
        tracker = Tracker(max_gap_s=2, min_hits=1)
        original = tracker.update([detection((10, 10, 30, 60))], 0)[0]
        original.velocity = (400, 0)
        recovered = tracker.update([detection((10, 10, 30, 60))], 1)[0]
        self.assertEqual(recovered.id, original.id)

    def test_extended_gap_recovers_but_does_not_report_hidden_worker(self):
        tracker = Tracker(max_gap_s=4, min_hits=1)
        original = tracker.update([detection()], 0)[0].id
        self.assertEqual(tracker.update([], 3), [])
        self.assertEqual(tracker.update([detection()], 3.5)[0].id, original)
        self.assertNotEqual(tracker.update([detection()], 8)[0].id, original)

    def test_distant_new_worker_does_not_claim_recoverable_id(self):
        tracker = Tracker(max_gap_s=4, min_hits=1)
        original = tracker.update([detection()], 0)[0].id
        newcomer = tracker.update([detection((300, 10, 320, 60))], 3)[0].id
        self.assertNotEqual(newcomer, original)
