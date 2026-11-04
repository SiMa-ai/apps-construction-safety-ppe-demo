import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.fetch_modalix_models import fetch, load_manifest, main


class ModelDownloadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b"test compiled artifact"
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.manifest = {
            "schema_version": 1,
            "target": "modalix",
            "release": "v0.1.0",
            "neat_version": "test-1",
            "platform_version": "test-1",
            "models": {
                "people": {
                    "filename": "people.tar.gz",
                    "sha256": self.sha,
                    "url": "https://docs.sima.ai/pkg_downloads/SDK2.1.3/models/modalix/yolo26-detection/people.tar.gz",
                },
                "ppe": {
                    "filename": "ppe.tar.gz",
                    "sha256": self.sha,
                    "url": "https://huggingface.co/example/demo/resolve/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/ppe.tar.gz",
                },
            },
        }

    def write(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(self.manifest))
        return path

    def test_complete_manifest_and_offline_cache(self):
        manifest = load_manifest(self.write())
        for model in manifest["models"].values():
            (self.root / model["filename"]).write_bytes(self.payload)
            with patch("scripts.fetch_modalix_models.urlopen") as network:
                self.assertTrue(fetch(model, self.root, verify_only=True).is_file())
                network.assert_not_called()

    def test_bad_metadata_rejected_before_any_download(self):
        self.manifest["models"]["ppe"]["sha256"] = None
        with patch("scripts.fetch_modalix_models.fetch") as download:
            with self.assertRaises(SystemExit) as error:
                main(["--manifest", str(self.write())])
            self.assertEqual(error.exception.code, 1)
            download.assert_not_called()

    def test_paths_and_mutable_urls_rejected(self):
        for field, value in [
            ("filename", "../ppe.tar.gz"),
            (
                "url",
                "http://huggingface.co/a/b/resolve/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/ppe.tar.gz",
            ),
            ("url", "https://huggingface.co/a/b/resolve/main/ppe.tar.gz"),
        ]:
            with self.subTest(field=field, value=value):
                original = self.manifest["models"]["ppe"][field]
                self.manifest["models"]["ppe"][field] = value
                with self.assertRaises(ValueError):
                    load_manifest(self.write())
                self.manifest["models"]["ppe"][field] = original

    def test_bad_download_preserves_existing_file(self):
        model = self.manifest["models"]["ppe"]
        path = self.root / model["filename"]
        path.write_bytes(b"previous artifact")
        response = io.BytesIO(b"wrong artifact")
        response.url = model["url"]
        with patch("scripts.fetch_modalix_models.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "Checksum"):
                fetch(model, self.root)
        self.assertEqual(path.read_bytes(), b"previous artifact")
        self.assertFalse(list(self.root.glob(".model-download-*")))

    def test_huggingface_download_replaces_only_after_verification(self):
        model = self.manifest["models"]["ppe"]
        response = io.BytesIO(self.payload)
        response.url = model["url"]
        with patch("scripts.fetch_modalix_models.urlopen", return_value=response):
            path = fetch(model, self.root)
        self.assertEqual(path.read_bytes(), self.payload)

    def test_model_zoo_uses_authenticated_cli(self):
        model = self.manifest["models"]["people"]

        def download(command, **kwargs):
            stage = Path(command[-1])
            (stage / model["filename"]).write_bytes(self.payload)
            self.assertEqual(command[:3], ["sima-cli", "download", model["url"]])
            self.assertTrue(kwargs["check"])

        with (
            patch("scripts.fetch_modalix_models.shutil.which", return_value="/usr/bin/sima-cli"),
            patch("scripts.fetch_modalix_models.subprocess.run", side_effect=download),
        ):
            self.assertEqual(fetch(model, self.root).read_bytes(), self.payload)

    def test_verify_only_missing_file_never_downloads(self):
        with patch("scripts.fetch_modalix_models.urlopen") as network:
            with self.assertRaises(ValueError):
                fetch(self.manifest["models"]["ppe"], self.root, verify_only=True)
            network.assert_not_called()
