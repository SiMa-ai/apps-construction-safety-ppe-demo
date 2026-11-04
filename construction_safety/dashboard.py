"""Local incident dashboard and camera controls; forward port 8765 from the SDK to your browser."""

import argparse
import gzip
import json
import re
import signal
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import cv2
import numpy as np

from .source_settings import SourceSettings
from .video_relay import VideoRelay
from .zone_settings import revision, save_zones

ROOT = Path(__file__).resolve().parents[1]
CAMERAS = tuple(
    json.loads((ROOT / "configs/dashboard.json").read_text()).get("cameras", ["src1", "src2"])
)
if not CAMERAS or any(not re.fullmatch(r"src[0-9]+", name) for name in CAMERAS):
    raise ValueError("Dashboard cameras must be source names such as src1")


def current_run(camera):
    """Resolve the camera run pointer, restricting reads to this repository's runs."""
    if camera not in CAMERAS:
        raise ValueError("Unknown camera")
    path = (ROOT / (ROOT / f"runs/{camera}.path").read_text().strip()).resolve()
    if not path.is_relative_to((ROOT / "runs").resolve()):
        raise ValueError("Invalid run path")
    return path


def recent_events(path, limit=200):
    # Read a bounded tail even after an overnight run. Discard partial edge records.
    with path.open("rb") as stream:
        stream.seek(0, 2)
        start = max(0, stream.tell() - 2_000_000)
        stream.seek(start)
        if start:
            stream.readline()
        lines = stream.read().splitlines()
    result = []
    for line in lines:
        try:
            record = json.loads(line)
            if record.get("schema_version") == 1:
                result.append(record)
        except (ValueError, TypeError):
            continue
    return result[-limit:]


_json_cache = {}
_json_lock = threading.Lock()


def shared_json(path):
    """Read shared state with brief retries, falling back to the last valid value.

    Cached summaries retain their producer timestamps so state() reports staleness.
    """
    # NFS-backed atomic replacements can briefly disappear to the SDK reader.
    for attempt in range(3):
        try:
            value = json.loads(path.read_text())
            with _json_lock:
                if len(_json_cache) > 64:
                    _json_cache.clear()
                _json_cache[str(path)] = value
            return value
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(0.03)
            else:
                with _json_lock:
                    if str(path) in _json_cache:
                        return _json_cache[str(path)]
                raise


def state():
    """Assemble camera health and incident snapshots in display-channel order."""
    cameras = []
    incidents = []
    for name in CAMERAS:
        try:
            run = current_run(name)
            summary = shared_json(run / "summary.json")
            age = (
                datetime.now(timezone.utc) - datetime.fromisoformat(summary["updated_at"])
            ).total_seconds()
            cameras.append({**summary, "source_id": name, "stale": age > 5, "age_s": round(age, 1)})
            collection = run / "incidents.json"
            incidents.extend(
                shared_json(collection)
                if collection.exists()
                else recent_events(run / "events.jsonl")
            )
        except (OSError, ValueError, KeyError) as exc:
            cameras.append(
                {"source_id": name, "status": "unavailable", "stale": True, "error": str(exc)}
            )
    incidents.sort(key=lambda e: e["timestamp_utc"], reverse=True)
    routes = source_settings.mappings()
    aliases = source_settings.aliases()
    for camera in cameras:
        camera["routing"] = routes[camera["source_id"]]
        camera["alias"] = aliases.get(camera["source_id"], "")
    cameras.sort(key=lambda c: c["routing"]["channel"])
    return {
        "cameras": cameras,
        "incidents": incidents[:300],
        "camera_job": dict(source_settings.job),
        "video_transport": dict(video_relay.status) if video_relay else {"mode": "direct"},
    }


video_relay = None

_preview_cache = {}
_preview_lock = threading.Lock()
_zone_lock = threading.Lock()
source_settings = SourceSettings(ROOT, CAMERAS, _zone_lock)


def small_preview(camera):
    """Return JPEG bytes, an ETag, and producer time from relay or shared storage."""
    if video_relay is not None:
        with video_relay.preview_lock:
            latest = video_relay.previews.get(camera)
        if latest is not None:
            # Retain the producer timestamp: stale relay frames must not look fresh.
            return latest
    for attempt in range(3):
        try:
            return _small_preview(camera)
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(0.03)
            else:
                with _preview_lock:
                    cached = _preview_cache.get(camera)
                    if cached and cached[0][0] == str(current_run(camera) / "preview.jpg"):
                        return cached[1], cached[2], cached[0][1] / 1e9
                raise


def _small_preview(camera):
    path = current_run(camera) / "preview.jpg"
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns)
    with _preview_lock:
        cached = _preview_cache.get(camera)
        if cached and cached[0] == key:
            return cached[1], cached[2], stat.st_mtime
        frame = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Preview is not a complete JPEG")
        if frame.shape[1] > 640:
            frame = cv2.resize(frame, (640, round(frame.shape[0] * 640 / frame.shape[1])))
        ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if not ok:
            raise ValueError("Preview encode failed")
        etag = '"' + str(stat.st_mtime_ns) + '"'
        data = data.tobytes()
        _preview_cache[camera] = (key, data, etag)
        return data, etag, stat.st_mtime


def webrtc_answer(request):
    """Proxy SDP to local Insight; media travels directly between Insight and browser."""
    if not isinstance(request, dict) or request.get("camera") not in CAMERAS:
        raise ValueError("Unknown camera")
    offer = request.get("offer")
    if not isinstance(offer, dict) or offer.get("type") != "offer":
        raise ValueError("Expected a WebRTC offer")
    if not isinstance(offer.get("sdp"), str) or not offer["sdp"].startswith("v=0"):
        raise ValueError("Invalid SDP")
    channel = source_settings.mappings()[request["camera"]]["channel"]
    if type(channel) is not int or not 0 <= channel < 4:
        raise ValueError("Invalid camera channel")
    # The destination is fixed, not browser supplied. Insight uses a local certificate.
    upstream = Request(
        f"https://127.0.0.1:8081/offer?channel={channel}",
        data=json.dumps({"type": "offer", "sdp": offer["sdp"]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(upstream, context=ssl._create_unverified_context(), timeout=10) as response:
            return response.read(), 200
    except HTTPError as exc:
        return json.dumps({"error": exc.read().decode(errors="replace")[:500]}).encode(), exc.code
    except (URLError, TimeoutError):
        return json.dumps({"error": "Insight signaling unavailable"}).encode(), 502


class Handler(BaseHTTPRequestHandler):
    """Serve static UI assets and camera, zone, incident, and preview endpoints."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, data, content_type="application/json", status=200):
        compressed = (
            content_type == "application/json"
            and len(data) > 1024
            and "gzip" in self.headers.get("Accept-Encoding", "")
        )
        if compressed:
            data = gzip.compress(data, compresslevel=1)
        self.send_response(status)
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if urlsplit(self.path).path not in (
            "/api/webrtc",
            "/api/review/reset",
            "/api/zones",
            "/api/camera-settings",
        ):
            self.send(b'{"error":"Not found"}', status=404)
            return
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.send(b'{"error":"Cross-origin reset denied"}', status=403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < size <= 65536
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"
            ):
                raise ValueError("Expected small JSON request")
            request = json.loads(self.rfile.read(size))
            if urlsplit(self.path).path == "/api/webrtc":
                answer, status = webrtc_answer(request)
                self.send(answer, status=status)
                return
            if urlsplit(self.path).path == "/api/camera-settings":
                self.send(json.dumps(source_settings.submit(request)).encode(), status=202)
                return
            if source_settings.job["status"] == "running":
                raise ValueError(
                    "Wait for the camera change to finish before editing zones or starting a review"
                )
            if urlsplit(self.path).path == "/api/zones":
                camera = request.get("camera")
                if camera not in CAMERAS:
                    raise ValueError("Unknown camera")
                with _zone_lock:
                    version = save_zones(
                        ROOT / f"configs/{camera}.json",
                        request.get("zones"),
                        request.get("revision"),
                    )
                self.send(json.dumps({"saved": True, "revision": version}).encode())
                return
            cameras = request.get("cameras", list(CAMERAS))
            if (
                not isinstance(cameras, list)
                or not cameras
                or any(c not in CAMERAS for c in cameras)
            ):
                raise ValueError("Invalid camera selection")
            targets = []
            for camera in set(cameras):
                run = current_run(camera)
                summary = shared_json(run / "summary.json")
                age = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(summary["updated_at"])
                ).total_seconds()
                if (
                    summary["status"] != "running"
                    or age > 5
                    or summary.get("review", {}).get("mode") != "demo_once"
                ):
                    raise ValueError(f"{camera} is not running in demo mode")
                targets.append(run)
            token = uuid.uuid4().hex
            for run in targets:
                tmp = run / f"review-request-{token}.tmp"
                tmp.write_text(json.dumps({"token": token}))
                tmp.replace(run / "review-request.json")
            self.send(json.dumps({"accepted": True, "token": token}).encode(), status=202)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.send(json.dumps({"error": str(exc)}).encode(), status=400)

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        query = parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                self.send(
                    (ROOT / "construction_safety/web/index.html").read_bytes(),
                    "text/html; charset=utf-8",
                )
            elif path in ("/dashboard.js", "/webrtc.js", "/dashboard.css", "/settings.js", "/source-settings.js"):
                kind = "text/javascript" if path.endswith(".js") else "text/css"
                self.send(
                    (ROOT / "construction_safety/web" / path[1:]).read_bytes(),
                    kind + "; charset=utf-8",
                )
            elif path in (
                "/fonts/ibm-plex-sans-400.woff2",
                "/fonts/ibm-plex-sans-500.woff2",
                "/fonts/ibm-plex-sans-600.woff2",
            ):
                self.send((ROOT / "construction_safety/web" / path[1:]).read_bytes(), "font/woff2")
            elif path == "/neat-mark.png":
                self.send((ROOT / "construction_safety/web/neat-mark.png").read_bytes(), "image/png")
            elif path == "/api/camera-settings":
                self.send(json.dumps(source_settings.snapshot()).encode())
            elif path == "/api/zones":
                camera = query.get("camera", [CAMERAS[0]])[0]
                if camera not in CAMERAS:
                    raise ValueError("Unknown camera")
                config = json.loads((ROOT / f"configs/{camera}.json").read_text())
                zones = config.get("zones", [])
                self.send(
                    json.dumps(
                        {"camera": camera, "zones": zones, "revision": revision(zones)}
                    ).encode()
                )
            elif path == "/api/editor-frame":
                run = current_run(query.get("camera", [CAMERAS[0]])[0])
                self.send((run / "editor.jpg").read_bytes(), "image/jpeg")
            elif path == "/api/state":
                self.send(json.dumps(state()).encode())
            elif path == "/api/incidents.json":
                self.send(json.dumps(state()["incidents"], indent=2).encode())
            elif path in ("/api/events.jsonl", "/api/performance.jsonl"):
                camera = query.get("camera", [CAMERAS[0]])[0]
                run = current_run(camera)
                # Capture a complete prefix and stream it without loading a long log into RAM.
                filename = "performance.jsonl" if path == "/api/performance.jsonl" else "events.jsonl"
                with (run / filename).open("rb") as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(0)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Content-Length", str(size))
                    self.send_header(
                        "Content-Disposition", f'attachment; filename="{camera}-{filename}"'
                    )
                    self.end_headers()
                    while size:
                        chunk = stream.read(min(size, 65536))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        size -= len(chunk)
            elif path == "/api/preview":
                data, etag, stamp = small_preview(query.get("camera", [CAMERAS[0]])[0])
                unchanged = self.headers.get("If-None-Match") == etag
                self.send_response(304 if unchanged else 200)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "private, no-cache")
                self.send_header("X-Frame-Time", str(stamp))
                if not unchanged:
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if not unchanged:
                    self.wfile.write(data)
            elif path == "/api/snapshot":
                camera = query.get("camera", [""])[0]
                session = query.get("session", [""])[0]
                incident = query.get("id", [""])[0]
                if (
                    camera not in CAMERAS
                    or not re.fullmatch(r"[a-f0-9]{12}", session)
                    or not re.fullmatch(r"[a-f0-9]{32}", incident)
                ):
                    raise ValueError("Invalid incident identifier")
                # Source + session keeps older incident links valid after a demo restart.
                found = None
                for run in (ROOT / "runs").glob(f"{camera}-*"):
                    summary = run / "summary.json"
                    if (
                        summary.exists()
                        and json.loads(summary.read_text()).get("session_id") == session
                    ):
                        found = run / "incidents" / f"{incident}.jpg"
                        break
                if found is None:
                    raise FileNotFoundError("Snapshot not found")
                self.send(found.read_bytes(), "image/jpeg")
            else:
                self.send(b'{"error":"Not found"}', status=404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (OSError, ValueError, KeyError) as exc:
            self.send(json.dumps({"error": str(exc)}).encode(), status=404)


def main():
    global video_relay
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Incident dashboard: http://{args.host}:{args.port}", flush=True)
    runtime = source_settings._device_runtime()
    if runtime and runtime.get("video_transport") == "ssh":
        video_relay = VideoRelay(ROOT, runtime)
        video_relay.start()

    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if video_relay:
            video_relay.close()


if __name__ == "__main__":
    main()
