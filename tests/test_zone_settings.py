import json
import tempfile
import unittest
from pathlib import Path

from construction_safety.zone_settings import revision, save_zones, validate_zones


class ZoneSettingsTests(unittest.TestCase):
    def zone(self, points=None):
        return {
            "name": "Danger",
            "level": 3,
            "restricted": True,
            "points": points or [[0.2, 0.2], [0.8, 0.2], [0.7, 0.8], [0.3, 0.8]],
        }

    def test_valid_trapezoid_and_empty_layout(self):
        self.assertEqual(validate_zones([self.zone()]), [self.zone()])
        self.assertEqual(validate_zones([]), [])

    def test_invalid_geometry_rejected(self):
        for points in (
            [[0, 0], [1, 1], [0, 1], [1, 0]],
            [[0, 0], [1, 0], [1, 0]],
            [[0, 0], [0.5, 0.5], [1, 1]],
            [[-0.1, 0], [1, 0], [0, 1]],
            [[0, 0], [float("nan"), 0], [0, 1]],
            [[0, 0], [1, 0]],
        ):
            with self.subTest(points=points), self.assertRaises(ValueError):
                validate_zones([self.zone(points)])

    def test_duplicate_names_rejected(self):
        with self.assertRaises(ValueError):
            validate_zones([self.zone(), self.zone()])

    def test_save_preserves_other_settings_and_rejects_stale_editor(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "src6.json"
            old = {"zones": [], "model": "model.pt", "ppe": {"enabled": True}}
            path.write_text(json.dumps(old))
            initial = revision([])
            version = save_zones(path, [self.zone()], initial)
            new = json.loads(path.read_text())
            self.assertEqual(new["model"], old["model"])
            self.assertEqual(new["ppe"], old["ppe"])
            self.assertEqual(version, revision([self.zone()]))
            self.assertEqual(len(list((path.parent / "zone-history").glob("*.json"))), 1)
            with self.assertRaises(ValueError):
                save_zones(path, [], initial)
            self.assertEqual(json.loads(path.read_text()), new)
