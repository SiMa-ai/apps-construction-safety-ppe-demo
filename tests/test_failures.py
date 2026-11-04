import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from construction_safety.app import main, parse_args


class FailureTests(unittest.TestCase):
    def test_missing_model_is_an_explicit_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                main(["--source", "missing.mp4", "--model", str(Path(directory) / "absent.pt")])

    def test_bad_source_is_not_treated_as_empty_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "placeholder.pt"
            model.touch()
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(RuntimeError, "Cannot open source"),
            ):
                main(
                    [
                        "--model",
                        str(model),
                        "--source",
                        str(root / "missing.mp4"),
                        "--run-dir",
                        str(root / "out"),
                    ]
                )

    def test_invalid_output_rate_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--source", "test.mp4", "--output-fps", "0"])

    def test_existing_event_log_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "placeholder.pt"
            model.touch()
            events = root / "events.jsonl"
            events.write_text("existing data\n")
            with self.assertRaises(FileExistsError):
                main(["--model", str(model), "--source", "unused.mp4", "--run-dir", directory])
            self.assertEqual(events.read_text(), "existing data\n")
