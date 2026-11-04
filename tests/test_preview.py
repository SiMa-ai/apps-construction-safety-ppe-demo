import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from construction_safety.dashboard import _preview_cache, small_preview


class PreviewTests(unittest.TestCase):
    def test_cached_small_preview_changes_when_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            _preview_cache.clear()
            cv2.imwrite(str(run / "preview.jpg"), np.zeros((720, 1280, 3), np.uint8))
            with patch("construction_safety.dashboard.current_run", return_value=run):
                data, etag, _ = small_preview("src6")
                self.assertEqual(
                    cv2.imdecode(np.frombuffer(data, np.uint8), 1).shape[:2], (360, 640)
                )
                self.assertEqual(small_preview("src6")[:2], (data, etag))
                cv2.imwrite(str(run / "new.jpg"), np.full((720, 1280, 3), 200, np.uint8))
                (run / "new.jpg").replace(run / "preview.jpg")
                new, newtag, _ = small_preview("src6")
                self.assertNotEqual(newtag, etag)
                self.assertNotEqual(new, data)
