import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from construction_safety.app import publish_dashboard_files


class PublicationTests(unittest.TestCase):
    def test_publishes_distinct_editor_and_annotated_preview_with_history(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / 'run'
            preview = Path(directory) / 'local'
            run.mkdir()
            preview.mkdir()
            raw = np.zeros((48, 64, 3), dtype=np.uint8)
            annotated = np.full_like(raw, 200)
            for i in range(2):
                payload = json.dumps({'frames': i}) + '\n'
                duration = publish_dashboard_files(run, preview, payload, payload, raw, annotated)
                self.assertGreaterEqual(duration, 0)
            self.assertEqual(json.loads((run / 'summary.json').read_text()), {'frames': 1})
            self.assertEqual(len((run / 'performance.jsonl').read_text().splitlines()), 2)
            self.assertEqual((run / 'preview.jpg').read_bytes(), (preview / 'preview.jpg').read_bytes())
            self.assertLess(cv2.imread(str(run / 'editor.jpg')).mean(), 1)
            self.assertGreater(cv2.imread(str(run / 'preview.jpg')).mean(), 190)
            self.assertFalse(list(run.glob('*.tmp*')))

    def test_failed_image_write_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            with patch('construction_safety.app.cv2.imwrite', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'preview'):
                    publish_dashboard_files(run, run, '{}', '{}', None, np.zeros((2, 2, 3)))
