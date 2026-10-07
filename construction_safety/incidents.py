"""Append-only JSON incident records and bounded-size visual evidence."""

import json
import math
import uuid
from collections import Counter
from datetime import datetime, timezone

import cv2


class IncidentLog:
    """Keep an append-only event history and a current incident snapshot.

    demo_once groups hazards within a finite review window; it does not identify
    the same person across video replays. continuous records each gated event.
    """

    def __init__(self, directory, session_id, source_id, channel, policy=None):
        self.directory = directory
        self.session_id, self.source_id, self.channel = session_id, source_id, channel
        self.policy = policy or {"mode": "continuous"}
        self.mode = self.policy.get("mode", "continuous")
        self.window_s = float(self.policy.get("collection_seconds", 0))
        if self.mode not in ("continuous", "demo_once") or (
            self.mode == "demo_once" and (not math.isfinite(self.window_s) or self.window_s <= 0)
        ):
            raise ValueError("demo_once requires a positive finite collection_seconds")
        self.review_id = uuid.uuid4().hex[:12]
        self.review_start_s = 0.0
        self.records = {}
        self.suppressed = 0
        self.control_token = None
        (directory / "incidents").mkdir(exist_ok=True)
        self.stream = (directory / "events.jsonl").open("x", buffering=1)
        self.publish()

    def write(self, event, frame_index, timestamp_s, image):
        """Create/update an incident, or return None after the demo review window."""
        if self.mode == "demo_once" and timestamp_s - self.review_start_s >= self.window_s:
            self.suppressed += 1
            return None
        # Omit person IDs from demo grouping: replay-created tracks join one hazard.
        key = (
            (event["type"], event.get("zone"), event.get("item"))
            if self.mode == "demo_once"
            else uuid.uuid4().hex
        )
        observed_at = datetime.now(timezone.utc).isoformat()
        if key in self.records:
            record = self.records[key]
            record["observation_count"] += 1
            record["last_observed_utc"] = observed_at
            record["last_timestamp_s"] = round(timestamp_s, 3)
            pid = event["person_id"]
            if pid not in record["person_ids"]:
                record["person_ids"].append(pid)
            record["latest_observation"] = {
                "person_id": pid,
                "timestamp_utc": observed_at,
                "timestamp_s": round(timestamp_s, 3),
                "worker_box": event.get("worker_box"),
                "ppe_status": event.get("ppe_status", {}),
            }
            record["revision"] += 1
            record["record_type"] = "incident_updated"
            self.stream.write(json.dumps(record) + "\n")
            self.publish()
            return dict(record)
        event_id = uuid.uuid4().hex
        snapshot = f"incidents/{event_id}.jpg"
        if not cv2.imwrite(str(self.directory / snapshot), image, [cv2.IMWRITE_JPEG_QUALITY, 82]):
            raise RuntimeError("Failed to save incident snapshot")
        pid = event["person_id"]
        record = {
            **event,
            "schema_version": 1,
            "incident_id": event_id,
            "session_id": self.session_id,
            "source_id": self.source_id,
            "channel": self.channel,
            "review_id": self.review_id,
            "record_type": "incident_created",
            "revision": 1,
            "grouping": "hazard_zone_item" if self.mode == "demo_once" else "individual_event",
            "observation_count": 1,
            "last_observed_utc": observed_at,
            "last_timestamp_s": round(timestamp_s, 3),
            "pid": f"{self.source_id}:{self.session_id}:{pid}",
            "person_ids": [pid],
            "severity": "danger" if event["level"] == 3 else "warning",
            "frame": frame_index,
            "timestamp_s": round(timestamp_s, 3),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "snapshot": snapshot,
            "box_coordinates": "pixels in source frame",
            "snapshot_size": [image.shape[1], image.shape[0]],
        }
        self.records[key] = record
        self.stream.write(json.dumps(record) + "\n")
        self.publish()
        return record

    def publish(self):
        """Replace the dashboard snapshot; JSONL retains all previous revisions."""
        tmp = self.directory / "incidents.tmp"
        tmp.write_text(json.dumps(list(self.records.values()), indent=2) + "\n")
        tmp.replace(self.directory / "incidents.json")

    def summary(self, now):
        elapsed = max(0.0, now - self.review_start_s)
        return {
            "mode": self.mode,
            "review_id": self.review_id,
            "collection_seconds": self.window_s,
            "elapsed_s": round(elapsed, 1),
            "complete": self.mode == "demo_once" and elapsed >= self.window_s,
            "incident_count": len(self.records),
            "counts": dict(Counter(r["type"] for r in self.records.values())),
            "replay_observations_ignored": self.suppressed,
        }

    def apply_control(self, now):
        """Consume each reset token once and archive the previous review snapshot."""
        path = self.directory / "review-request.json"
        if not path.exists():
            return
        request = json.loads(path.read_text())
        if request["token"] == self.control_token:
            return
        archive = self.directory / "reviews"
        archive.mkdir(exist_ok=True)
        (archive / f"{self.review_id}.json").write_text(
            json.dumps(list(self.records.values()), indent=2) + "\n"
        )
        self.control_token = request["token"]
        self.review_id = uuid.uuid4().hex[:12]
        self.review_start_s = now
        self.records = {}
        self.suppressed = 0
        self.publish()
        return True

    def close(self):
        self.stream.close()
