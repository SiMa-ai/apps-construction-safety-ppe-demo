import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from construction_safety.dashboard import webrtc_answer


class WebRTCTests(unittest.TestCase):
    def test_rejects_unknown_camera_and_non_offer(self):
        requests = (
            None,
            {},
            {"camera": "not-a-camera"},
            {"camera": "src1", "offer": {"type": "answer"}},
        )
        for request in requests:
            with self.assertRaises(ValueError):
                webrtc_answer(request)

    def test_uses_saved_channel_and_fixed_destination(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"type":"answer","sdp":"test"}'
        with (
            patch(
                "construction_safety.dashboard.source_settings.mappings",
                return_value={"src1": {"channel": 1}},
            ),
            patch("construction_safety.dashboard.urlopen", return_value=response) as opened,
        ):
            answer, status = webrtc_answer(
                {"camera": "src1", "offer": {"type": "offer", "sdp": "v=0\r\n"}}
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(answer)["type"], "answer")
        self.assertEqual(
            opened.call_args.args[0].full_url, "https://127.0.0.1:8081/offer?channel=1"
        )

    def test_preserves_retryable_upstream_status(self):
        error = HTTPError("", 503, "Waiting", {}, BytesIO(b"Waiting for video"))
        with (
            patch(
                "construction_safety.dashboard.source_settings.mappings",
                return_value={"src1": {"channel": 0}},
            ),
            patch("construction_safety.dashboard.urlopen", side_effect=error),
        ):
            answer, status = webrtc_answer(
                {"camera": "src1", "offer": {"type": "offer", "sdp": "v=0\r\n"}}
            )
        self.assertEqual(status, 503)
        self.assertIn("Waiting", json.loads(answer)["error"])
