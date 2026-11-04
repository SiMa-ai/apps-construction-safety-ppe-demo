"""Check the public Neat box contract without requiring an accelerator."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from construction_safety.detectors import NeatDetector


class NeatDetectorTests(unittest.TestCase):
    def detector(self, rows):
        detector = NeatDetector.__new__(NeatDetector)
        detector.config = {"score": 0.35, "score_by_category": {"ppe": 0.5}}
        detector.class_map = {
            "5": {"label": "Person", "category": "worker"},
            "4": {"label": "NO-Safety Vest", "category": "ppe"},
        }
        detector.pyneat = SimpleNamespace(
            Tensor=SimpleNamespace(from_numpy=Mock(return_value=object())),
            PixelFormat=SimpleNamespace(BGR=1),
            TensorMemory=SimpleNamespace(EV74=1),
            decode_bbox=Mock(return_value=[SimpleNamespace(to_numpy=lambda: np.asarray(rows))]),
        )
        detector.model = SimpleNamespace(run=Mock(return_value=[object()]))
        return detector

    def test_xyxy_coordinates_and_ppe_threshold(self):
        detector = self.detector(
            [
                [-5, 10, 80, 60, 0.8, 5],
                [5, 5, 20, 20, 0.4, 4],
                [5, 5, 20, 20, 0.6, 4],
                [0, 0, 10, 10, 0.9, 6],
            ]
        )
        results = detector.detect(np.zeros((100, 100, 3), np.uint8))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].box, (0, 10, 80, 60))
        self.assertEqual(results[1].label, "NO-Safety Vest")

    def test_timeout_is_not_empty_scene(self):
        detector = self.detector([])
        detector.model.run.return_value = []
        with self.assertRaisesRegex(RuntimeError, "no result"):
            detector.detect(np.zeros((100, 100, 3), np.uint8))
