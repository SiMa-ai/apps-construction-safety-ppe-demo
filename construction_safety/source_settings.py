"""Dashboard-owned source routing, using Insight's media control API."""

import json
import os
import shlex
import ssl
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .zone_settings import revision, save_zones


def insight(path, payload=None):
    # Insight's local installation uses a development certificate. This endpoint
    # is fixed server-side; browsers cannot supply a proxy destination.
    request = Request(
        "https://127.0.0.1:9900/api/" + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, context=ssl._create_unverified_context(), timeout=10) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise ValueError("Insight: " + exc.read().decode()[:500]) from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError("Insight is unavailable: " + str(exc)) from exc
    if isinstance(result, dict) and result.get("error"):
        raise ValueError("Insight: " + str(result["error"]))
    return result


class SourceSettings:
    def __init__(self, root, cameras, zone_lock):
        self.root, self.cameras, self.zone_lock = Path(root), cameras, zone_lock
        self.lock = threading.Lock()
        self.job = {"status": "idle"}

    def mappings(self):
        config = json.loads((self.root / "configs/dashboard.json").read_text())
        routes = config.get("routes", {})
        return {
            name: routes.get(name, {"source": int(name[3:]), "channel": n})
            for n, name in enumerate(self.cameras)
        }

    def snapshot(self):
        insight("health")
        sources = insight("mediasrc")
        videos = insight("mediasrc/videos")
        routes = self.mappings()
        return {
            "routes": routes,
            "revision": revision(routes),
            "sources": sources,
            "videos": videos,
            "channels": list(range(4)),
            "job": dict(self.job),
        }

    def submit(self, request):
        if not isinstance(request, dict):
            raise ValueError("Expected a camera settings object")
        if not self.lock.acquire(blocking=False):
            raise ValueError("Another camera change is in progress. Wait for it to finish.")
        try:
            state = self.snapshot()
            if request.get("revision") != state["revision"]:
                raise ValueError("Camera routing changed. Refresh settings and try again.")
            camera = request.get("camera")
            if camera not in self.cameras:
                raise ValueError("Unknown camera")
            if request.get("action") not in ("apply", "stop"):
                raise ValueError("Unknown action")
            routes = state["routes"]
            old = routes[camera]
            source = request.get("source")
            channel = request.get("channel")
            if type(source) is not int or source not in [s["index"] for s in state["sources"]]:
                raise ValueError("Select an available Insight source")
            if type(channel) is not int or channel not in state["channels"]:
                raise ValueError("Select a display channel from 0 to 3")
            if any(r["source"] == source for name, r in routes.items() if name != camera):
                raise ValueError("This source is already used by another demo camera")
            target = next(s for s in state["sources"] if s["index"] == source)
            current = next(s for s in state["sources"] if s["index"] == old["source"])
            if request["action"] == "stop":
                if source != old["source"] or channel != old["channel"]:
                    raise ValueError("Stop uses the current saved routing. Discard edits first.")
            else:
                if request.get("video") not in state["videos"]:
                    raise ValueError("Select a video from the Insight library")
                # A source may be in use outside this demo. Do not silently replace it.
                if source != old["source"] and target["state"] == "playing":
                    raise ValueError(
                        "Selected source is playing outside this camera. Choose a stopped source."
                    )
            previous_video = old.get("video", current["file"])
            scene_changed = request["action"] == "apply" and request["video"] != previous_video
            if scene_changed and request.get("reset_zones") is not True:
                raise ValueError(
                    "A different video needs new zones. Select Clear zones for new video."
                )
            job_id = uuid.uuid4().hex
            self.job = {
                "id": job_id,
                "camera": camera,
                "status": "running",
                "message": "Applying camera settings…",
            }
            threading.Thread(
                target=self._run, args=(dict(request), state, scene_changed), daemon=True
            ).start()
            return dict(self.job)
        except Exception:
            self.lock.release()
            raise

    def _device_runtime(self):
        runtime = json.loads((self.root / "configs/dashboard.json").read_text()).get("runtime", {})
        return runtime if runtime.get("backend") == "neat-device" else None

    def _device_command(self, action, camera, route=None):
        runtime = self._device_runtime()
        command = [runtime["python"], str(self.root / "scripts/device_camera.py"), action, camera]
        if route is not None:
            command += ["--source", str(route["source"]), "--channel", str(route["channel"])]
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                runtime["ssh_target"],
                shlex.join(command),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    def _process_alive(self, camera):
        if self._device_runtime():
            return self._device_command("status", camera)["alive"]
        try:
            pid = int((self.root / f"runs/{camera}.pid").read_text())
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            return b"construction_safety.app" in args and camera.encode() in args
        except (OSError, ValueError):
            return False

    def _stop(self, camera):
        if self._device_runtime():
            self._device_command("stop", camera)
            return
        if not self._process_alive(camera):
            return
        subprocess.run(
            ["bash", "scripts/stop_insight.sh"],
            cwd=self.root,
            env={**os.environ, "INSTANCE": camera},
            check=True,
            capture_output=True,
            timeout=10,
        )
        for _ in range(150):
            if not self._process_alive(camera):
                return
            time.sleep(0.2)
        raise ValueError(f"{camera} has not stopped yet. Retry after it exits.")

    def _start(self, camera, route):
        if self._device_runtime():
            record = self._device_command("start", camera, route)
            run = Path(record["run_dir"]).resolve()
            if not run.is_relative_to((self.root / "runs").resolve()):
                raise ValueError("Device returned an invalid run directory")
            pointer = self.root / f"runs/{camera}.path"
            temporary = pointer.with_suffix(".tmp")
            temporary.write_text(str(run.relative_to(self.root.resolve())) + "\n")
            temporary.replace(pointer)
        else:
            subprocess.run(
                ["bash", "scripts/start_insight.sh", "--config", f"configs/{camera}.json"],
                cwd=self.root,
                env={
                    **os.environ,
                    "INSTANCE": camera,
                    "SOURCE": f"rtsp://127.0.0.1:8554/src{route['source']}",
                    "CHANNEL": str(route["channel"]),
                },
                check=True,
                capture_output=True,
                timeout=10,
            )
        run = self.root / (self.root / f"runs/{camera}.path").read_text().strip()
        for _ in range(120):
            try:
                summary = json.loads((run / "summary.json").read_text())
                if summary.get("status") == "running" and summary.get("frames", 0) > 0:
                    return
            except (OSError, ValueError):
                pass
            if not self._process_alive(camera):
                raise ValueError(f"{camera} failed to start. See {run.name}/monitor.log.")
            time.sleep(0.5)
        raise ValueError(
            f"{camera} has not produced frames yet. Check its monitor.log before retrying."
        )

    def _run(self, request, state, scene_changed):
        camera = request["camera"]
        routes = state["routes"]
        old = dict(routes[camera])
        record = {
            "id": self.job["id"],
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "camera": camera,
            "request": request,
            "before": json.loads(json.dumps(routes)),
        }
        try:
            if request["action"] == "stop":
                self._stop(camera)
                insight("mediasrc/stop", {"index": old["source"]})
            else:
                swapped = next(
                    (
                        n
                        for n, r in routes.items()
                        if n != camera and r["channel"] == request["channel"]
                    ),
                    None,
                )
                restart_swapped = swapped and self._process_alive(swapped)
                self._stop(camera)
                if swapped:
                    self._stop(swapped)
                    routes[swapped]["channel"] = old["channel"]
                if old["source"] != request["source"]:
                    insight("mediasrc/stop", {"index": old["source"]})
                target = next(s for s in state["sources"] if s["index"] == request["source"])
                if target["file"] != request["video"] or target["transport"] != "rtsp":
                    insight(
                        "mediasrc/assign",
                        {"index": request["source"], "file": request["video"], "transport": "rtsp"},
                    )
                actual = next(s for s in insight("mediasrc") if s["index"] == request["source"])
                if actual["state"] != "playing":
                    insight("mediasrc/start", {"index": request["source"]})
                    actual = next(s for s in insight("mediasrc") if s["index"] == request["source"])
                if actual["state"] != "playing" or actual["file"] != request["video"]:
                    raise ValueError("Insight did not apply the requested source assignment")
                if scene_changed:
                    with self.zone_lock:
                        path = self.root / f"configs/{camera}.json"
                        config = json.loads(path.read_text())
                        save_zones(path, [], revision(config.get("zones", [])))
                routes[camera] = {
                    "source": request["source"],
                    "channel": request["channel"],
                    "video": request["video"],
                }
                path = self.root / "configs/dashboard.json"
                config = json.loads(path.read_text())
                config["routes"] = routes
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(config, indent=2) + "\n")
                tmp.replace(path)
                for name in [camera] + ([swapped] if swapped else []):
                    with self.zone_lock:
                        config_path = self.root / f"configs/{name}.json"
                        camera_config = json.loads(config_path.read_text())
                        route = routes[name]
                        video = route.get(
                            "video",
                            next(
                                s["file"] for s in state["sources"] if s["index"] == route["source"]
                            ),
                        )
                        camera_config["input_media"] = {
                            "source": route["source"],
                            "video": video,
                            "channel": route["channel"],
                        }
                        temp = config_path.with_suffix(".tmp")
                        temp.write_text(json.dumps(camera_config, indent=2) + "\n")
                        temp.replace(config_path)
                if restart_swapped:
                    self._start(swapped, routes[swapped])
                self._start(camera, routes[camera])
            self.job = {
                **self.job,
                "status": "done",
                "message": "Camera stopped."
                if request["action"] == "stop"
                else "Camera is live. Draw zones for the new video, then start a new review."
                if scene_changed
                else "Camera is live. Routing applied.",
            }
        except Exception as exc:
            self.job = {
                **self.job,
                "status": "error",
                "message": str(exc)
                + " Some steps may already have applied; refresh settings to inspect current state.",
            }
        finally:
            record.update(result=dict(self.job), after=self.mappings())
            try:
                with (self.root / "runs/camera-settings.jsonl").open("a") as stream:
                    stream.write(json.dumps(record) + "\n")
            finally:
                self.lock.release()
