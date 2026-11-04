"""Regression cases for partial figures, sampled PPE and fragmented person IDs."""

import unittest

import numpy as np

from construction_safety.analytics import Analytics, Detection, Track, Tracker, minimum_cost_matches
from construction_safety.detectors import tracking_config


def person(box=(30, 20, 70, 170), ident="W1", score=0.9):
    return Track(ident, box, score, "person", "worker", 0)


def observation(box=(30, 20, 70, 170), score=0.9):
    return Detection(box, score, "person", "worker")


def analytics(ppe=None):
    return Analytics(
        {
            "event_hold_s": 0.1,
            "zone_membership": {"mode": "ground_contact"},
            "ppe": {"enabled": True, "log_missing": True, **(ppe or {})},
            "zones": [{"name": "DANGER", "level": 3, "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
        },
        200,
        200,
    )


NEGATIVE = [Detection((40, 25, 60, 45), 0.9, "NO-Hardhat", "ppe")]


class VisibilityTests(unittest.TestCase):
    def test_short_clear_view_can_confirm_vest_at_sampled_frame_rate(self):
        a = analytics({"hold_s": 0.5, "min_observations": 3})
        worker = person()
        vest = [Detection((35, 60, 65, 115), 0.9, "NO-Safety Vest", "ppe")]
        for now in [0, 0.3]:
            self.assertFalse(any(e["type"] == "ppe_missing" for e in a.update([worker], now, vest)))
            a.update([worker], now + 0.1, ppe_updated=False)
        events = a.update([worker], 0.6, vest)
        self.assertEqual([(e["type"], e.get("item")) for e in events], [("ppe_missing", "vest")])
        self.assertEqual(a.summary()["ppe_missing_now"], 1)

    def test_short_confirmation_still_rejects_partial_vest_observations(self):
        a = analytics({"hold_s": 0.5, "min_observations": 3})
        worker = person()
        vest = [Detection((35, 60, 65, 115), 0.9, "NO-Safety Vest", "ppe")]
        a.update([worker], 0, vest)
        a.update([worker], 0.3, vest)
        worker.box = (30, 0, 70, 170)
        a.update([worker], 0.4, ppe_updated=False)
        for now in [0.6, 0.9, 1.2]:
            self.assertEqual(a.update([worker], now, vest), [])
        self.assertEqual(a.summary()["ppe_missing_now"], 0)
        worker.box = (30, 20, 70, 170)
        self.assertFalse(any(e["type"] == "ppe_missing" for e in a.update([worker], 1.5, vest)))

    def test_clipping_at_any_border_cannot_log_ppe_or_zone_incidents(self):
        for box in [(0, 20, 70, 170), (30, 0, 70, 170), (30, 20, 199, 170), (30, 20, 70, 199)]:
            with self.subTest(box=box):
                a, worker = analytics(), person(box)
                events = []
                for now in [0, 0.5, 1.1, 2]:
                    events.extend(a.update([worker], now, NEGATIVE))
                self.assertEqual(events, [])
                self.assertEqual(a.occupancy["workers_partial"], 1)
                self.assertEqual(a.occupancy["workers_total"], 1)
                self.assertEqual(a.occupancy["workers_assessable"], 0)
                self.assertEqual(a.occupancy["red_zone_out"], 0)
                self.assertEqual(a.status["W1"]["ppe"]["hardhat"], "unknown")

    def test_overlapping_people_are_preserved_but_not_assessed(self):
        a = analytics()
        workers = [person(), person((50, 20, 90, 170), "W2")]
        a.update(workers, 0, NEGATIVE)
        self.assertEqual(a.update(workers, 2, NEGATIVE), [])
        self.assertEqual(a.occupancy["workers_total"], 2)
        self.assertEqual(a.occupancy["workers_partial"], 2)
        self.assertTrue(
            all(
                "possible_person_occlusion" in s["visibility"]["reasons"] for s in a.status.values()
            )
        )

    def test_tiny_wide_and_weak_people_are_uncertain(self):
        for worker in [person((30, 20, 34, 30)), person((30, 20, 170, 80)), person(score=0.35)]:
            a = analytics()
            a.update([worker], 0, NEGATIVE)
            self.assertEqual(a.update([worker], 2, NEGATIVE), [])
            self.assertEqual(a.occupancy["workers_uncertain"], 1)

    def test_sudden_body_truncation_is_partial(self):
        tracker = Tracker(min_hits=1)
        tracker.update([observation()], 0)
        tracks = tracker.update([observation((30, 20, 70, 90))], 0.1)
        a = analytics()
        a.update(tracks, 0.1)
        self.assertIn("box_height_drop", a.status[tracks[0].id]["visibility"]["reasons"])

    def test_full_view_retains_real_danger_and_repeated_ppe_alerts(self):
        a, worker = analytics(), person()
        self.assertEqual(a.update([worker], 0, NEGATIVE), [])
        events = a.update([worker], 0.5, NEGATIVE)
        self.assertEqual([e["type"] for e in events], ["danger_zone_entry"])
        self.assertEqual(a.summary()["ppe_missing_now"], 0)
        events = a.update([worker], 1.1, NEGATIVE)
        self.assertEqual([e["type"] for e in events], ["ppe_missing"])
        self.assertTrue(events[0]["visibility"]["incident_eligible"])
        self.assertFalse(events[0]["visibility"]["full_body_verified"])
        self.assertEqual(a.summary()["ppe_missing_now"], 1)

    def test_unsampled_frames_cannot_confirm_ppe(self):
        a, worker = analytics(), person()
        a.update([worker], 0, NEGATIVE)
        for now in np.arange(0.1, 1.1, 0.1):
            self.assertFalse(
                any(
                    e["type"] == "ppe_missing"
                    for e in a.update([worker], float(now), ppe_updated=False)
                )
            )
        # One additional fresh observation still does not reach three samples.
        self.assertFalse(any(e["type"] == "ppe_missing" for e in a.update([worker], 1.1, NEGATIVE)))

    def test_partial_on_unsampled_frame_clears_evidence_and_restarts_dwell(self):
        a, worker = analytics(), person()
        a.update([worker], 0, NEGATIVE)
        a.update([worker], 0.5, NEGATIVE)
        worker.box = (30, 0, 70, 170)
        a.update([worker], 0.6, ppe_updated=False)
        self.assertEqual(a.ppe_status, {})
        worker.box = (30, 20, 70, 170)
        a.update([worker], 0.7, ppe_updated=False)
        self.assertEqual(a.status["W1"]["missing_ppe"], [])
        for now in [1.0, 1.5]:
            self.assertFalse(
                any(e["type"] == "ppe_missing" for e in a.update([worker], now, NEGATIVE))
            )
        self.assertTrue(any(e["type"] == "ppe_missing" for e in a.update([worker], 2.1, NEGATIVE)))

    def test_positive_or_unknown_ppe_interrupts_negative_sequence(self):
        for observations in [[], [Detection((40, 25, 60, 45), 0.9, "Hardhat", "ppe")]]:
            a, worker = analytics(), person()
            a.update([worker], 0, NEGATIVE)
            a.update([worker], 0.5, observations)
            self.assertFalse(
                any(e["type"] == "ppe_missing" for e in a.update([worker], 1.1, NEGATIVE))
            )


class IdentityTests(unittest.TestCase):
    def test_unsampled_machine_frames_do_not_interrupt_confirmation(self):
        tracker = Tracker(min_hits=2)
        machine = Detection((10, 10, 100, 100), 0.8, "machinery", "machine")
        self.assertEqual(tracker.update([machine], 0), [])
        self.assertEqual(tracker.update([], 0.1, machines_updated=False), [])
        self.assertEqual(tracker.update([machine], 0.2)[0].id, "M0001")

    def test_weak_person_maintains_existing_id_but_cannot_create_one(self):
        tracker = Tracker(min_hits=1)
        self.assertEqual(tracker.update([observation(score=0.3)], 0), [])
        original = tracker.update([observation()], 0.1)[0].id
        weak = tracker.update([observation(score=0.3)], 0.2)
        self.assertEqual(weak[0].id, original)
        self.assertEqual(tracker.update([observation()], 0.3)[0].id, original)
        self.assertEqual(len(tracker.confirmed["worker"]), 1)

    def test_tentative_flicker_does_not_consume_ids_or_accumulate_confirmation(self):
        tracker = Tracker(min_hits=3)
        for now in [0, 1, 2]:
            self.assertEqual(tracker.update([observation()], now), [])
            tracker.update([], now + 0.1)
        self.assertEqual(tracker.update([observation()], 3), [])
        self.assertEqual(tracker.update([observation()], 3.1), [])
        self.assertEqual(tracker.update([observation()], 3.2)[0].id, "W0001")

    def test_global_assignment_preserves_both_feasible_tracks(self):
        costs = np.array([[0.2, 0.3], [0.21, 1e6]])
        self.assertEqual(set(minimum_cost_matches(costs)), {(0, 1), (1, 0)})
        self.assertEqual(minimum_cost_matches(np.array([[1e6]])), [])

    def test_two_neighbours_keep_ids_when_greedy_choice_would_create_a_third(self):
        tracker = Tracker(min_hits=1)
        original = tracker.update([observation((20, 10, 40, 60)), observation((50, 10, 70, 60))], 0)
        ids = [p.id for p in original]
        tracks = tracker.update([observation((30, 10, 50, 60)), observation((0, 10, 20, 60))], 0.1)
        by_box = {t.box: t.id for t in tracks}
        self.assertEqual(by_box[(0, 10, 20, 60)], ids[0])
        self.assertEqual(by_box[(30, 10, 50, 60)], ids[1])
        self.assertEqual(len(tracker.confirmed["worker"]), 2)

    def test_detector_keeps_recovery_boxes_without_lowering_ppe_threshold(self):
        original = {"score": 0.35, "score_by_category": {"worker": 0.5, "ppe": 0.6}}
        config = tracking_config(original)
        self.assertEqual(config["score"], 0.25)
        self.assertEqual(config["score_by_category"], {"worker": 0.25, "ppe": 0.6})
        self.assertEqual(original["score_by_category"]["worker"], 0.5)
