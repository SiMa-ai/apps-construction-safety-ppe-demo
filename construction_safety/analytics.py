"""Session-local tracking, polygon occupancy, and debounced incident candidates.

Times are seconds on the source clock; boxes use source-image pixel coordinates.
IncidentLog separately groups and persists the candidates emitted here.
"""

import math
from collections import Counter
from dataclasses import dataclass, field

import cv2
import numpy as np

from .red_zone import associate_ppe, guard_missing_vests, zone_for_box

# OpenCV uses BGR: safe, caution, PPE warning, and danger.
COLORS = {0: (60, 210, 60), 1: (0, 230, 255), 2: (0, 140, 255), 3: (0, 0, 255)}


@dataclass
class Detection:
    """One model observation with an (x1, y1, x2, y2) box in source pixels."""

    box: tuple
    score: float
    label: str
    category: str


@dataclass
class Track:
    """A session-local worker or machine ID, retained across short detection gaps."""

    id: str
    box: tuple
    score: float
    label: str
    category: str
    last_seen: float
    hits: int = 1
    velocity: tuple = (0.0, 0.0)
    votes: Counter = field(default_factory=Counter)

    @property
    def foot(self):
        return ((self.box[0] + self.box[2]) / 2, self.box[3])


def iou(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    total = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - area
    return area / total if total > 0 else 0.0


class Tracker:
    """One-to-one association within worker/machine categories, with short-gap recovery."""

    def __init__(self, max_gap_s=1.5, min_hits=2):
        self.max_gap_s, self.min_hits = max_gap_s, min_hits
        self.tracks = []
        self.next_id = Counter()
        self.confirmed = {"worker": set(), "machine": set()}

    def update(self, detections, now):
        """Return confirmed tracks observed now; keep unseen tracks only for recovery."""
        self.tracks = [t for t in self.tracks if now - t.last_seen <= self.max_gap_s]
        candidates = []
        for ti, t in enumerate(self.tracks):
            dt = max(0.0, now - t.last_seen)
            vx, vy = t.velocity
            predicted = tuple(v + (vx if i % 2 == 0 else vy) * dt for i, v in enumerate(t.box))
            for di, d in enumerate(detections):
                if t.category != d.category:
                    continue
                overlap = iou(predicted, d.box)
                cx = (d.box[0] + d.box[2] - predicted[0] - predicted[2]) / 2
                cy = (d.box[1] + d.box[3] - predicted[1] - predicted[3]) / 2
                size = max(20.0, t.box[3] - t.box[1])
                distance = math.hypot(cx, cy) / size
                if overlap >= 0.15 or distance <= 0.6:
                    candidates.append((1 - overlap + 0.3 * distance, ti, di))
        # Greedy lowest-cost matches are one-to-one within each object category.
        used_t, used_d = set(), set()
        for _, ti, di in sorted(candidates):
            if ti in used_t or di in used_d:
                continue
            t, d = self.tracks[ti], detections[di]
            dt = max(0.001, now - t.last_seen)
            dx = (d.box[0] + d.box[2] - t.box[0] - t.box[2]) / 2 / dt
            dy = (d.box[1] + d.box[3] - t.box[1] - t.box[3]) / 2 / dt
            t.velocity = (0.5 * t.velocity[0] + 0.5 * dx, 0.5 * t.velocity[1] + 0.5 * dy)
            t.box, t.score, t.last_seen = d.box, d.score, now
            t.hits += 1
            t.votes[d.label] += 1
            t.label = t.votes.most_common(1)[0][0]
            used_t.add(ti)
            used_d.add(di)
        for di, d in enumerate(detections):
            if di in used_d:
                continue
            self.next_id[d.category] += 1
            ident = ("W" if d.category == "worker" else "M") + f"{self.next_id[d.category]:04d}"
            t = Track(ident, d.box, d.score, d.label, d.category, now)
            t.votes[d.label] = 1
            self.tracks.append(t)
        visible = [t for t in self.tracks if t.last_seen == now and t.hits >= self.min_hits]
        for t in visible:
            self.confirmed[t.category].add(t.id)
        return visible


def load_zones(config, width, height):
    """Convert normalized vertices to pixels, with highest-priority zones first."""
    zones = []
    names = set()
    for z in config.get("zones", []):
        name = z["name"]
        if name in names:
            raise ValueError(f"Duplicate zone: {name}")
        names.add(name)
        points = np.asarray(z["points"], dtype=float)
        if (
            points.ndim != 2
            or points.shape[1] != 2
            or len(points) < 3
            or not np.isfinite(points).all()
        ):
            raise ValueError(f"Invalid polygon: {name}")
        if np.any(points < 0) or np.any(points > 1):
            raise ValueError(f"Zone {name} must use normalized coordinates")
        polygon = np.round(points * [width, height]).astype(np.int32)
        if abs(cv2.contourArea(polygon)) < 1:
            raise ValueError(f"Empty or self-crossing polygon: {name}")
        level = int(z.get("level", 3 if z.get("restricted") else 0))
        if level not in COLORS:
            raise ValueError(f"Invalid severity for {name}")
        zones.append({**z, "polygon": polygon, "level": level})
    return sorted(zones, key=lambda z: (z["level"], bool(z.get("restricted"))), reverse=True)


def zone_for(point, zones):
    return next(
        (
            z
            for z in zones
            if cv2.pointPolygonTest(z["polygon"], tuple(map(float, point)), False) >= 0
        ),
        None,
    )


class Gate:
    """Emit once on confirmed entry or escalation; clear after sustained absence."""

    def __init__(self, hold_s=0.3, release_s=0.6):
        self.hold_s, self.release_s = hold_s, release_s
        self.states = {}

    def update(self, observations, now):
        events = []
        for key, (level, payload) in observations.items():
            if level <= 0:
                continue
            s = self.states.get(key)
            if s is None:
                s = {"candidate": level, "since": now, "peak": 0, "last": now}
                self.states[key] = s
            if level != s["candidate"]:
                s["candidate"], s["since"] = level, now
            s["last"] = now
            if level > s["peak"] and now - s["since"] >= self.hold_s:
                s["peak"] = level
                events.append({**payload, "level": level})
        for key in list(self.states):
            if now - self.states[key]["last"] > self.release_s:
                del self.states[key]
        return events


class Analytics:
    """Current occupancy and separate, debounced danger/PPE incidents. No paths."""

    def __init__(self, config, width, height):
        self.zones = load_zones(config, width, height)
        self.width, self.height = width, height
        self.membership = config.get(
            "zone_membership", {"mode": "bottom_center", "include_boundary": True}
        )
        if self.membership.get("mode", "bottom_center") not in (
            "bottom_center",
            "either_bottom_corner",
        ):
            raise ValueError("Invalid zone_membership mode")
        self.ppe_enabled = config.get("ppe", {}).get("enabled", False)
        self.ppe_log_missing = config.get("ppe", {}).get("log_missing", False)
        self.ppe_status = {}
        self.vest_guard = config.get("ppe", {}).get("high_visibility_vest_guard", {})
        self.ppe_visual_evidence = {}
        self.ppe_last_observed = None
        self.ppe_max_age_s = config.get("ppe", {}).get("max_age_s", 2.0)
        self.red_worker_ids = []
        self.occupancy = {"workers_total": 0, "red_zone_in": 0, "red_zone_out": 0}
        self.red_peak = 0
        self.gate = Gate(config.get("event_hold_s", 0.3), config.get("event_release_s", 1.0))
        self.ppe_gate = Gate(config.get("event_hold_s", 0.3), config.get("event_release_s", 1.0))
        self.counts = Counter()
        self.status = {}

    def update(self, tracks, now, ppe_detections=(), ppe_updated=True, frame=None):
        """Update occupancy and emit confirmed danger/PPE observations.

        Unsampled PPE frames may display recent evidence, but cannot advance the
        PPE incident gate. Evidence expires or is dropped when a worker disappears.
        """
        workers = [t for t in tracks if t.category == "worker"]
        if ppe_updated:
            self.ppe_status = associate_ppe(workers, ppe_detections) if self.ppe_enabled else {}
            self.ppe_visual_evidence = guard_missing_vests(
                frame, workers, self.ppe_status, self.vest_guard
            )
            self.ppe_last_observed = now
        else:
            visible = {w.id for w in workers}
            self.ppe_status = {
                pid: state for pid, state in self.ppe_status.items() if pid in visible
            }
            self.ppe_visual_evidence = {
                pid: value for pid, value in self.ppe_visual_evidence.items() if pid in visible
            }
            if self.ppe_last_observed is None or now - self.ppe_last_observed > self.ppe_max_age_s:
                self.ppe_status = {}
                self.ppe_visual_evidence = {}
        self.red_worker_ids, self.status = [], {}
        danger, ppe = {}, {}
        for w in workers:
            z = zone_for_box(w.box, self.zones, **self.membership)
            zone_name = z["name"] if z else "OPEN"
            in_danger = bool(z and (z.get("restricted") or z["level"] == 3))
            missing = [
                item for item, state in self.ppe_status.get(w.id, {}).items() if state == "missing"
            ]
            self.status[w.id] = {
                "zone": zone_name,
                "danger": in_danger,
                "missing_ppe": missing,
                "ppe": self.ppe_status.get(w.id, {"hardhat": "unknown", "vest": "unknown"}),
                "ppe_visual_evidence": self.ppe_visual_evidence.get(w.id, {}),
            }
            payload = {
                "worker_id": w.id,
                "person_id": w.id,
                "worker_box": list(w.box),
                "zone": zone_name,
                "ppe_status": self.ppe_status.get(w.id, {}),
                "ppe_visual_evidence": self.ppe_visual_evidence.get(w.id, {}),
            }
            if in_danger:
                self.red_worker_ids.append(w.id)
                danger[(w.id, zone_name)] = (3, {**payload, "type": "danger_zone_entry"})
            if ppe_updated and self.ppe_log_missing:
                for item in missing:
                    ppe[(w.id, item)] = (
                        2,
                        {
                            **payload,
                            "type": "ppe_missing",
                            "item": item,
                            "evidence": "explicit negative class",
                        },
                    )
        self.occupancy = {
            "workers_total": len(workers),
            "red_zone_in": len(self.red_worker_ids),
            "red_zone_out": len(workers) - len(self.red_worker_ids),
        }
        self.red_peak = max(self.red_peak, len(self.red_worker_ids))
        events = self.gate.update(danger, now)
        if ppe_updated:
            events += self.ppe_gate.update(ppe, now)
        for event in events:
            self.counts[event["type"]] += 1
        return events

    def begin_review(self):
        """Rearm incident gates without resetting IDs, occupancy, or lifetime counts."""
        self.gate.states.clear()
        self.ppe_gate.states.clear()

    def summary(self):
        return {
            "occupancy": self.occupancy,
            "red_zone_peak": self.red_peak,
            "ppe_status": self.ppe_status,
            "zone_membership": self.membership,
            "event_counts": dict(self.counts),
            "people": [{"person_id": pid, **state} for pid, state in self.status.items()],
            "ppe_missing_now": sum(bool(s["missing_ppe"]) for s in self.status.values()),
        }

    def render(self, frame, tracks, fps):
        out = frame.copy()
        overlay = out.copy()
        for z in self.zones:
            if z.get("restricted") or z["level"] == 3:
                cv2.fillPoly(overlay, [z["polygon"]], COLORS[3])
        out = cv2.addWeighted(overlay, 0.09, out, 0.91, 0)
        for z in self.zones:
            if z.get("restricted") or z["level"] == 3:
                cv2.polylines(out, [z["polygon"]], True, COLORS[3], 2)
        for t in tracks:
            if t.category != "worker":
                continue
            state = self.status.get(t.id, {})
            danger, missing = state.get("danger", False), state.get("missing_ppe", [])
            color = COLORS[3] if danger else (COLORS[2] if missing else (210, 210, 210))
            x1, y1, x2, y2 = map(int, t.box)
            if danger:
                tint = out.copy()
                cv2.rectangle(tint, (x1, y1), (x2, y2), color, -1)
                out = cv2.addWeighted(tint, 0.18, out, 0.82, 0)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 3 if danger else 2)
            label = t.id + (" DANGER" if danger else (" PPE" if missing else ""))
            if danger and missing:
                label += " | PPE"
            y = max(22, y1 - 6)
            cv2.putText(
                out, label, (max(0, x1), y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15, 15, 15), 4
            )
            cv2.putText(out, label, (max(0, x1), y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
        count = len(self.red_worker_ids)
        text = f"DANGER ZONE: {count}" if count else "DANGER ZONE: CLEAR"
        cv2.rectangle(out, (8, 8), (310, 44), (20, 20, 20), -1)
        cv2.putText(
            out,
            text,
            (16, 33),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            COLORS[3] if count else (225, 225, 225),
            2,
        )
        return out
