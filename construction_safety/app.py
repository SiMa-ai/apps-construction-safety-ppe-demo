"""Run one camera: capture, inference, tracking, incidents, and preview publication.

Camera policy and detector profiles are merged at startup. Only zone edits are
reloaded during a run. Run with python -m construction_safety.app --help.
"""

import argparse
import json
import os
import signal
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import cv2

from .analytics import Analytics, Tracker, load_zones
from .detectors import (
    NeatDetector,
    NeatYolo26PPETrackerDetector,
    Yolo26PPETrackerDetector,
    YoloDetector,
)
from .incidents import IncidentLog
from .media import FFmpegSender, NeatSender, Source
from .zone_settings import revision, validate_zones

ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Construction danger-zone and PPE incident monitor")
    p.add_argument("--source", required=True, help="Video file or RTSP URL")
    p.add_argument("--source-id", default="camera", help="Camera label included in incident IDs")
    p.add_argument("--config", type=Path, default=ROOT / "configs/camera.example.json")
    p.add_argument("--backend", choices=["yolo", "neat"], default="yolo")
    p.add_argument(
        "--detector-config",
        type=Path,
        help="Detector profile overriding model settings while preserving camera zones",
    )
    p.add_argument(
        "--model", type=Path, help="Override the checkpoint selected by the configuration"
    )
    p.add_argument("--device", default="cpu")
    p.add_argument(
        "--run-dir", type=Path, default=ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    p.add_argument("--frames", type=int, default=0)
    p.add_argument(
        "--start-seconds", type=float, default=0, help="Seek into a video file before processing"
    )
    p.add_argument(
        "--duration",
        type=float,
        default=0,
        help="Maximum processing wall time, seconds; 0 runs until stopped",
    )
    p.add_argument("--insight-host", help="Video receiver; SDK-local default address is 127.0.0.1")
    p.add_argument("--video-port-base", type=int, default=9000)
    p.add_argument("--channel", type=int, default=1)
    p.add_argument("--output-fps", type=float, default=10)
    p.add_argument("--preview-width", type=int, default=1280)
    p.add_argument("--preview-dir", type=Path, help="Optional device-local latest-frame directory")
    p.add_argument(
        "--record",
        action="store_true",
        help="Write annotated MP4 segments (mp4v); playback rate is output-fps",
    )
    p.add_argument("--segment-seconds", type=int, default=120)
    p.add_argument(
        "--realtime",
        action="store_true",
        help="Pace video-file processing to its source timestamps",
    )
    a = p.parse_args(argv)
    if (
        a.start_seconds < 0
        or a.frames < 0
        or a.duration < 0
        or a.output_fps <= 0
        or a.preview_width < 2
        or a.segment_seconds <= 0
    ):
        p.error("Invalid frame/time/output geometry settings")
    if not 0 <= a.channel < 80 or not 1 <= a.video_port_base + a.channel <= 65535:
        p.error("Invalid Insight channel or video port")
    return a


def main(argv=None):
    args = parse_args(argv)
    config = json.loads(args.config.read_text())
    # A model profile overrides inference settings without replacing camera policy.
    if args.detector_config:
        profile = json.loads(args.detector_config.read_text())
        config["detector"] = profile["detector"]
        config["model"] = profile["model"]
        config["pipeline"] = profile.get("pipeline", "single")
        config.pop("auxiliary_detector", None)
        if "auxiliary_detector" in profile:
            config["auxiliary_detector"] = profile["auxiliary_detector"]
    if args.model is None:
        args.model = ROOT / config.get("model", "models/construction-world.pt")
    if not args.model.is_file():
        raise FileNotFoundError(f"Missing detector model: {args.model}")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = args.preview_dir or args.run_dir
    preview_dir.mkdir(parents=True, exist_ok=True)
    if (args.run_dir / "events.jsonl").exists():
        raise FileExistsError(
            "Choose a fresh --run-dir; existing session artifacts will not be overwritten"
        )
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    run_id = uuid.uuid4().hex[:12]
    source = sender = writer = events = None
    running = True
    frames = 0
    failure = None
    analytics = tracker = detector = None
    frame = rendered = None
    start = time.monotonic()
    first_timestamp = None
    now = 0.0
    last_checkpoint = 0.0
    zone_version = revision(config.get("zones", []))
    zone_error = None
    inference_times = deque(maxlen=1000)
    detection_counts = {}

    def stop(_signum, _frame):
        nonlocal running
        running = False

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)

    def checkpoint(final=False):
        """Publish dashboard state atomically and an unannotated zone-editor frame."""
        if analytics is None:
            return
        summary = {
            **analytics.summary(),
            "session_id": run_id,
            "frames": frames,
            "status": "failed" if failure else ("finished" if final else "running"),
            "error": failure,
            "backend": args.backend,
            "model": str(args.model),
            "detector_labels": sorted(detector.labels) if detector else [],
            "detections_by_label": detection_counts,
            "unique_tracks": {k: len(v) for k, v in tracker.confirmed.items()} if tracker else {},
            "tracking_scope": "this video session; IDs may change after long occlusion",
            "source_id": args.source_id,
            "channel": args.channel,
            "input_source": args.source,
            "input_media": config.get("input_media"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": config.get("pipeline", "single"),
            "auxiliary_model": config.get("auxiliary_detector", {}).get("model"),
            "ppe_every_n_frames": config.get("auxiliary_detector", {}).get("every_n_frames", 1),
            "elapsed_wall_s": round(time.monotonic() - start, 3),
            "review": events.summary(now) if events else {},
            "zones_revision": zone_version,
            "zones_error": zone_error,
            "mean_inference_ms": round(
                1000 * sum(inference_times) / max(1, len(inference_times)), 2
            ),
        }
        tmp = args.run_dir / "summary.tmp"
        tmp.write_text(json.dumps(summary, indent=2) + "\n")
        tmp.replace(args.run_dir / "summary.json")
        if frame is not None:
            editor = cv2.resize(
                frame,
                (
                    min(960, frame.shape[1]),
                    round(frame.shape[0] * min(960, frame.shape[1]) / frame.shape[1]),
                ),
            )
            tmp_image = args.run_dir / "editor.tmp.jpg"
            if cv2.imwrite(str(tmp_image), editor, [cv2.IMWRITE_JPEG_QUALITY, 85]):
                tmp_image.replace(args.run_dir / "editor.jpg")

    try:
        source = Source(args.source)
        if args.start_seconds:
            if source.live:
                raise ValueError("--start-seconds applies only to video files")
            if not source.cap.set(cv2.CAP_PROP_POS_MSEC, args.start_seconds * 1000):
                raise RuntimeError("Source does not support seeking")
            source.index = round(args.start_seconds * source.fps)
        item = source.read()
        if item is None:
            raise RuntimeError("Source contains no frames")
        frame, stamp = item
        height, width = frame.shape[:2]
        analytics = Analytics(config, width, height)
        tracker = Tracker(**config.get("tracker", {}))
        if args.backend == "neat":
            if config.get("pipeline") == "yolo26-ppe":
                raise ValueError(
                    "YOLO26 hybrid profile uses PyTorch; no compiled Neat YOLO26 decoder is configured"
                )
            if config.get("pipeline") == "neat-yolo26-ppe":
                detector = NeatYolo26PPETrackerDetector(args.model, config, width, height, ROOT)
            else:
                detector = NeatDetector(args.model, config["detector"], width, height)
        elif config.get("pipeline") == "neat-yolo26-ppe":
            raise ValueError("The compiled two-model profile requires --backend neat")
        elif config.get("pipeline") == "yolo26-ppe":
            detector = Yolo26PPETrackerDetector(args.model, config, args.device, ROOT)
        else:
            detector = YoloDetector(args.model, config["detector"], args.device)
        out_w = min(width, args.preview_width) // 2 * 2
        out_h = max(2, round(height * out_w / width) // 2 * 2)
        if args.insight_host:
            port = args.video_port_base + args.channel
            sender = (
                NeatSender(out_w, out_h, args.output_fps, args.insight_host, port)
                if args.backend == "neat"
                else FFmpegSender(
                    out_w,
                    out_h,
                    args.output_fps,
                    args.insight_host,
                    port,
                    args.run_dir / "encoder.log",
                )
            )
        events = IncidentLog(
            args.run_dir, run_id, args.source_id, args.channel, config.get("incident_policy")
        )
        (args.run_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "session": run_id,
                    "source": args.source,
                    "backend": args.backend,
                    "model": str(args.model),
                    "classes": sorted(detector.labels),
                    "run_dir": str(args.run_dir),
                }
            ),
            flush=True,
        )
        start = time.monotonic()
        first_timestamp = stamp
        while running and item is not None:
            frame, stamp = item
            # Source timestamps drive tracking and review windows; UTC is for the log.
            now = max(0.0, stamp - first_timestamp)
            if events.apply_control(now):
                analytics.begin_review()
            begin = time.monotonic()
            detections = detector.detect(frame)
            inference_times.append(time.monotonic() - begin)
            for d in detections:
                detection_counts[d.label] = detection_counts.get(d.label, 0) + 1
            tracks = tracker.update(
                [d for d in detections if d.category in ("worker", "machine")], now
            )
            incidents = analytics.update(
                tracks,
                now,
                [d for d in detections if d.category == "ppe"],
                ppe_updated=getattr(detector, "ppe_updated", True),
                frame=frame,
            )
            fps = 1 / max(1e-6, time.monotonic() - begin)
            annotated = analytics.render(frame, tracks, fps)
            rendered = cv2.resize(annotated, (out_w, out_h))
            # Atomic latest-frame publication; preview delivery never queues old frames.
            tmp_image = preview_dir / "preview.tmp.jpg"
            if not cv2.imwrite(str(tmp_image), rendered, [cv2.IMWRITE_JPEG_QUALITY, 80]):
                raise RuntimeError("Cannot publish dashboard preview")
            tmp_image.replace(preview_dir / "preview.jpg")
            for event in incidents:
                record = events.write(
                    {
                        **event,
                        "source_size": [width, height],
                        "zones_revision": zone_version,
                        "input_source": args.source,
                        "input_media": config.get("input_media"),
                    },
                    frames,
                    now,
                    rendered,
                )
                if record is not None:
                    print(json.dumps(record), flush=True)
            if sender:
                sender.push(rendered, frames)
            if args.record:
                segment_frames = max(1, round(args.segment_seconds * args.output_fps))
                if frames % segment_frames == 0:
                    if writer:
                        writer.release()
                    writer = cv2.VideoWriter(
                        str(args.run_dir / f"annotated-{frames // segment_frames:04d}.mp4"),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        args.output_fps,
                        (out_w, out_h),
                    )
                    if not writer.isOpened():
                        raise RuntimeError("Cannot create output video")
                writer.write(rendered)
            frames += 1
            if time.monotonic() - last_checkpoint >= 1:
                if preview_dir != args.run_dir:
                    backup = args.run_dir / "preview.tmp.jpg"
                    backup.write_bytes((preview_dir / "preview.jpg").read_bytes())
                    backup.replace(args.run_dir / "preview.jpg")
                try:
                    new_zones = validate_zones(json.loads(args.config.read_text()).get("zones", []))
                    new_version = revision(new_zones)
                    if new_version != zone_version:
                        # Rearm entry gates for new geometry; retain the incident review.
                        analytics.zones = load_zones({"zones": new_zones}, width, height)
                        analytics.begin_review()
                        config["zones"] = new_zones
                        zone_version = new_version
                        (args.run_dir / "config.json").write_text(
                            json.dumps(config, indent=2) + "\n"
                        )
                    zone_error = None
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    zone_error = str(exc)
                checkpoint()
                last_checkpoint = time.monotonic()
            if (
                (args.frames and frames >= args.frames)
                or (args.duration and time.monotonic() - start >= args.duration)
            ):
                break
            if args.realtime and not source.live:
                wait = (source.index / source.fps - first_timestamp) - (time.monotonic() - start)
                if wait > 0:
                    time.sleep(min(wait, 1.0))
            item = source.read()
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # Each closer runs even if another resource fails to shut down.
        for obj in (writer, sender, source, events):
            if obj is not None:
                try:
                    obj.release() if obj is writer else obj.close()
                except Exception as exc:
                    print(f"Cleanup: {exc}", flush=True)
        checkpoint(final=True)
        print(
            json.dumps(
                {"session": run_id, "frames": frames, "status": "failed" if failure else "finished"}
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
