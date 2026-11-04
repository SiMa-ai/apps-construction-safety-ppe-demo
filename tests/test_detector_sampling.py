import unittest
from pathlib import Path
from unittest.mock import patch

from construction_safety.analytics import Detection
from construction_safety.detectors import Yolo26PPETrackerDetector


class FakeDetector:
    def __init__(self, path, config, device):
        self.primary = str(path).endswith("nano.pt")
        self.labels = {"person"} if self.primary else {"NO-Hardhat", "NO-Safety Vest"}
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        return [
            Detection(
                (0, 0, 20, 40),
                0.9,
                "person" if self.primary else "NO-Hardhat",
                "worker" if self.primary else "ppe",
            )
        ]


class SamplingTests(unittest.TestCase):
    @patch("construction_safety.detectors.YoloDetector", FakeDetector)
    def test_ppe_is_sampled_without_replaying_stale_detections(self):
        d = Yolo26PPETrackerDetector(
            Path("nano.pt"),
            {"detector": {}, "auxiliary_detector": {"model": "ppe.pt", "every_n_frames": 3}},
            "cpu",
            Path("."),
        )
        outputs = []
        timed_ppe_calls = []
        for _ in range(7):
            outputs.append(d.detect(None))
            timed_ppe_calls.append("ppe_detector" in d.last_timings)
        self.assertEqual(timed_ppe_calls, [True, False, False, True, False, False, True])
        self.assertEqual([len(ds) for ds in outputs], [2, 1, 1, 2, 1, 1, 2])
        self.assertEqual(d.primary.calls, 7)
        self.assertEqual(d.auxiliary.calls, 3)

    @patch("construction_safety.detectors.YoloDetector", FakeDetector)
    def test_invalid_sample_interval_fails(self):
        with self.assertRaises(ValueError):
            Yolo26PPETrackerDetector(
                Path("nano.pt"),
                {"detector": {}, "auxiliary_detector": {"model": "ppe.pt", "every_n_frames": 0}},
                "cpu",
                Path("."),
            )


class CompiledSamplingTests(unittest.TestCase):
    def test_compiled_pair_uses_neat_and_filters_auxiliary_people(self):
        import tempfile
        from unittest.mock import Mock

        from construction_safety.detectors import NeatYolo26PPETrackerDetector

        primary = Mock(labels={"person"})
        worker = Detection((0, 0, 20, 40), 0.9, "person", "worker")
        ppe = Detection((0, 0, 20, 40), 0.9, "NO-Hardhat", "ppe")
        primary.detect.side_effect = lambda frame: [worker]
        auxiliary = Mock(labels={"NO-Hardhat", "NO-Safety Vest"})
        auxiliary.detect.side_effect = lambda frame: [worker, ppe]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ppe.tar.gz").touch()
            with (
                patch(
                    "construction_safety.detectors.NeatDetector", side_effect=[primary, auxiliary]
                ),
                patch("construction_safety.detectors.YoloDetector") as cpu,
            ):
                detector = NeatYolo26PPETrackerDetector(
                    root / "people.tar.gz",
                    {
                        "detector": {},
                        "auxiliary_detector": {"model": "ppe.tar.gz", "every_n_frames": 3},
                    },
                    640,
                    480,
                    root,
                )
                outputs, sampled = [], []
                for _ in range(7):
                    outputs.append(detector.detect(None))
                    sampled.append(detector.ppe_updated)
                self.assertEqual([len(rows) for rows in outputs], [2, 1, 1, 2, 1, 1, 2])
                self.assertEqual(sampled, [True, False, False, True, False, False, True])
                self.assertTrue(
                    all(sum(d.category == "worker" for d in rows) == 1 for rows in outputs)
                )
                self.assertEqual(primary.detect.call_count, 7)
                self.assertEqual(auxiliary.detect.call_count, 3)
                cpu.assert_not_called()
