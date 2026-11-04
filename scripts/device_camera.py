#!/usr/bin/env python3
"""Device-side lifecycle helper for dashboard-owned cameras on a shared workspace."""

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def identity(pid, camera):
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if b"construction_safety.app" not in args or camera.encode() not in args:
            return None
        return stat[19] if stat[0] != "Z" else None
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("camera")
    parser.add_argument("--source", type=int)
    parser.add_argument("--channel", type=int)
    args = parser.parse_args()
    if not re.fullmatch(r"src[0-9]+", args.camera):
        parser.error("Invalid camera name")
    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    with (runs / f"{args.camera}.device.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record_path = runs / f"{args.camera}.device.json"
        record = json.loads(record_path.read_text()) if record_path.exists() else {}
        pid = record.get("pid", 0)
        alive = bool(
            pid
            and identity(pid, args.camera) == record.get("start_ticks")
            and record.get("start_ticks")
        )
        if args.action == "status":
            print(json.dumps({"alive": alive, **record}))
            return
        if args.action == "stop":
            if alive:
                os.kill(pid, signal.SIGTERM)
                for _ in range(100):
                    if identity(pid, args.camera) != record["start_ticks"]:
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError(
                        "Camera did not stop after 20 seconds; inspect the device process"
                    )
            record_path.unlink(missing_ok=True)
            print(json.dumps({"alive": False}))
            return
        if alive:
            raise RuntimeError("Camera already running")
        if args.source is None or args.source < 1 or args.channel not in range(4):
            parser.error("Start requires a valid source and channel")
        runtime = json.loads((ROOT / "configs/dashboard.json").read_text())["runtime"]
        profile = ROOT / runtime["detector_config"]
        config = json.loads(profile.read_text())
        for model in (config["model"], config["auxiliary_detector"]["model"]):
            if not (ROOT / model).is_file():
                raise FileNotFoundError(model)
        run = runs / (args.camera + "-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
        run.mkdir()
        preview_dir = Path("/tmp/construction-safety-preview") / run.name
        command = [
            str(Path(runtime["python"]).expanduser()),
            "-u",
            "-m",
            "construction_safety.app",
            "--backend",
            "neat",
            "--config",
            str(ROOT / f"configs/{args.camera}.json"),
            "--detector-config",
            str(profile),
            "--source",
            f"rtsp://{runtime['insight_host']}:{runtime['rtsp_port']}/src{args.source}",
            "--insight-host",
            runtime.get("video_host", runtime["insight_host"]),
            "--video-port-base",
            str(runtime["video_port_base"]),
            "--channel",
            str(args.channel),
            "--source-id",
            args.camera,
            "--run-dir",
            str(run),
            "--preview-width",
            str(runtime.get("preview_width", 960)),
        ]
        command += ["--preview-dir", str(preview_dir)]
        with (run / "monitor.log").open("w") as log:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        for _ in range(50):
            ticks = identity(process.pid, args.camera)
            if ticks:
                break
            if process.poll() is not None:
                raise RuntimeError(f"Camera exited; see {run}/monitor.log")
            time.sleep(0.02)
        if not ticks:
            process.terminate()
            raise RuntimeError("Cannot establish camera process identity")
        record = {
            "pid": process.pid,
            "start_ticks": ticks,
            "run_dir": str(run),
            "preview_dir": str(preview_dir),
        }
        record_path.write_text(json.dumps(record) + "\n")
        print(json.dumps({"alive": True, **record}))


if __name__ == "__main__":
    main()
