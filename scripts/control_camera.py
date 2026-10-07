#!/usr/bin/env python3
"""Start, stop, or inspect a configured camera on its selected runtime."""

import argparse
import json
import re
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prompt(label, default="", validate=lambda value: bool(value)):
    while True:
        value = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip() or default
        if validate(value):
            return value
        print("Invalid value; please try again.")


def configure(root, force=False):
    directory = root / "configs"
    paths = [directory / name for name in ("dashboard.json", "src1.json", "src2.json")]
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        raise ValueError(
            "Configuration already exists. Edit it or use configure --force to back it up and replace it."
        )
    dashboard = json.loads((directory / "dashboard.example.json").read_text())
    camera = json.loads((directory / "camera.example.json").read_text())
    runtime = dashboard["runtime"]
    print("Use real device/SDK addresses, not the placeholder values. No credentials are stored.")

    def host_valid(value):
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", value)) and value not in (
            "SDK_HOST",
            "DEVICE_HOST",
        )

    user = prompt(
        "Device SSH user", "sima", lambda v: bool(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", v))
    )
    host = prompt("Device SSH hostname or IPv4 address", validate=host_valid)
    runtime["ssh_target"] = f"{user}@{host}"
    runtime["python"] = prompt(
        "Absolute path to device Python with pyneat",
        f"/home/{user}/pyneat/bin/python",
        lambda v: v.startswith("/") and "USER" not in v,
    )
    runtime["insight_host"] = prompt(
        "SDK hostname or IPv4 address reachable from the device", validate=host_valid
    )

    def port_valid(v):
        return v.isdigit() and 1 <= int(v) <= 65535

    runtime["rtsp_port"] = int(
        prompt("Insight RTSP host port (see neat --json)", "8554", port_valid)
    )
    transport = prompt("Video transport: ssh or direct", "ssh", lambda v: v in ("ssh", "direct"))
    runtime["video_transport"] = transport
    if transport == "ssh":
        runtime["video_host"] = "127.0.0.1"
        runtime["video_port_base"] = 29000
        runtime["insight_local_video_port_base"] = int(
            prompt(
                "Insight video UDP base inside SDK",
                "9000",
                lambda v: port_valid(v) and int(v) <= 65532,
            )
        )
    else:
        runtime["video_host"] = runtime["insight_host"]
        runtime["video_port_base"] = int(
            prompt(
                "Insight video UDP host base (see neat --json)",
                "9000",
                lambda v: port_valid(v) and int(v) <= 65532,
            )
        )
    generated = {paths[0]: dashboard}
    for index, name in enumerate(dashboard["cameras"]):
        video = prompt(
            f"{name} Insight media-library path (Enter to select later in dashboard)",
            validate=lambda v: not v.startswith("/") and ".." not in Path(v).parts,
        )
        route = dashboard["routes"][name]
        route["video"] = video
        cfg = dict(camera, input_media=dict(route))
        generated[paths[index + 1]] = cfg
    if existing:
        backup = (
            root
            / "runs"
            / ("config-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
        )
        backup.mkdir(parents=True)
        for path in existing:
            shutil.copy2(path, backup / path.name)
        print(f"Previous settings backed up to {backup}")
    for path, value in generated.items():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.replace(path)
    print(
        "Saved local dashboard/src1/src2 settings. Add compiled archives to models/, select videos, then draw zones. No danger zones are enabled yet."
    )


def main():
    from construction_safety.source_settings import SourceSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["configure", "start", "stop", "restart", "status"])
    parser.add_argument("camera", nargs="?", help="Configured camera name, or all")
    parser.add_argument(
        "--force", action="store_true", help="Back up and replace existing config when configuring"
    )
    args = parser.parse_args()
    if args.action == "configure":
        try:
            configure(ROOT, args.force)
        except (ValueError, EOFError, KeyboardInterrupt) as exc:
            parser.exit(1, f"Configuration not completed: {exc}\n")
        return
    if not args.camera or args.force:
        parser.error("Specify a camera or all; --force is only for configure")
    if not (ROOT / "configs/dashboard.json").is_file():
        parser.error("Run python3 scripts/control_camera.py configure first")
    config = json.loads((ROOT / "configs/dashboard.json").read_text())
    cameras = config["cameras"] if args.camera == "all" else [args.camera]
    if any(camera not in config["cameras"] for camera in cameras):
        parser.error("Camera is not configured in dashboard.json")
    manager = SourceSettings(ROOT, config["cameras"], threading.Lock())
    for camera in cameras:
        if args.action in ("stop", "restart"):
            manager._stop(camera)
        if args.action in ("start", "restart"):
            if not manager._process_alive(camera):
                manager._start(camera, manager.mappings()[camera])
        print(json.dumps({"camera": camera, "running": manager._process_alive(camera)}))


if __name__ == "__main__":
    main()
