"""Polygon membership and PPE association for incident monitoring."""

import cv2


def zone_for_box(box, zones, mode="either_bottom_corner", include_boundary=True):
    """Return the first matching zone; callers must supply zones in priority order.

    Bottom anchors approximate ground contact in the image, not physical depth.
    """
    if mode not in ("bottom_center", "either_bottom_corner"):
        raise ValueError(f"Unknown zone membership mode: {mode}")
    x1, _, x2, y2 = box
    points = [((x1 + x2) / 2, y2)] if mode == "bottom_center" else [(x1, y2), (x2, y2)]
    for zone in zones:
        for point in points:
            value = cv2.pointPolygonTest(zone["polygon"], tuple(map(float, point)), False)
            if value > 0 or include_boundary and value == 0:
                return zone
    return None


def ground_contact(box, zones, width, height, margin_fraction=0.02):
    """Estimate ground membership from the box's bottom center, with uncertainty.

    This is image geometry, not measured depth or detected foot keypoints. The
    boundary tolerance scales with person height as a perspective heuristic.
    """
    import math

    if not math.isfinite(margin_fraction) or not 0 <= margin_fraction <= 0.25:
        raise ValueError("ground_margin_fraction must be between 0 and 0.25")
    x1, y1, x2, y2 = map(float, box)
    anchor = ((x1 + x2) / 2, y2)
    evidence = {
        "method": "box_bottom_center",
        "point_px": list(anchor),
        "state": "outside",
        "zone": None,
        "depth_measured": False,
    }
    if y2 >= height - 2:
        return None, {**evidence, "state": "uncertain", "reason": "feet_outside_frame"}
    margin = max(1.0, (y2 - y1) * margin_fraction)
    evidence["boundary_margin_px"] = round(margin, 2)
    for zone in zones:
        distance = cv2.pointPolygonTest(zone["polygon"], anchor, True)
        if distance < -margin:
            continue
        evidence.update(zone=zone["name"], boundary_distance_px=round(distance, 2))
        if distance <= margin:
            # Do not fall through to an overlapping safe zone at a danger boundary.
            return None, {**evidence, "state": "uncertain", "reason": "near_zone_boundary"}
        return zone, {**evidence, "state": "inside"}
    return None, evidence


PPE_LABELS = {
    "Hardhat": ("hardhat", "present"),
    "NO-Hardhat": ("hardhat", "missing"),
    "Safety Vest": ("vest", "present"),
    "NO-Safety Vest": ("vest", "missing"),
}


def associate_ppe(workers, detections):
    """Associate PPE by body region and overlap; missing detections stay unknown.

    Explicit positive and negative evidence for the same item produces conflict.
    Ambiguous ownership between overlapping workers contributes no evidence.
    """
    result = {w.id: {"hardhat": "unknown", "vest": "unknown"} for w in workers}
    evidence = {}
    for d in detections:
        if d.label not in PPE_LABELS:
            continue
        item, state = PPE_LABELS[d.label]
        x1, y1, x2, y2 = d.box
        area = max(1.0, (x2 - x1) * (y2 - y1))
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        candidates = []
        for w in workers:
            a, b, c, e = w.box
            if not a <= cx <= c or not b <= cy <= e:
                continue
            fraction = (cy - b) / max(1.0, e - b)
            if (
                item == "hardhat"
                and fraction > 0.45
                or item == "vest"
                and not 0.15 <= fraction <= 0.85
            ):
                continue
            overlap = max(0, min(x2, c) - max(x1, a)) * max(0, min(y2, e) - max(y1, b)) / area
            if overlap >= 0.6:
                candidates.append((overlap, -abs(cx - (a + c) / 2) / max(1, c - a), w.id))
        if not candidates:
            continue
        candidates.sort(reverse=True)
        # Overlapping people can make ownership ambiguous; do not assign arbitrarily.
        if (
            len(candidates) > 1
            and abs(candidates[0][0] - candidates[1][0]) < 0.05
            and abs(candidates[0][1] - candidates[1][1]) < 0.1
        ):
            continue
        worker_id = candidates[0][2]
        evidence.setdefault((worker_id, item), set()).add(state)
    for (worker_id, item), states in evidence.items():
        result[worker_id][item] = next(iter(states)) if len(states) == 1 else "conflict"
    return result


def guard_missing_vests(frame, workers, states, config):
    """Change missing-vest states to conflict when torso color contradicts the model.

    Mutates states in place and returns the supporting measurements. Color alone
    never marks a vest present or certifies that the worker has appropriate PPE.
    """
    evidence = {}
    if frame is None or not config.get("enabled", False):
        return evidence
    import numpy as np

    height, width = frame.shape[:2]
    for worker in workers:
        state = states.get(worker.id, {})
        if state.get("vest") != "missing":
            continue
        x1, y1, x2, y2 = worker.box
        w, h = x2 - x1, y2 - y1
        left, right = max(0, int(x1 + 0.08 * w)), min(width, int(x1 + 0.92 * w))
        top, bottom = max(0, int(y1 + 0.18 * h)), min(height, int(y1 + 0.65 * h))
        if right - left < 12 or bottom - top < 12:
            continue
        roi = frame[top:bottom, left:right]
        hue, saturation, value = cv2.split(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV))
        lime = (hue >= 20) & (hue <= 85) & (saturation >= 55) & (value >= 130)
        orange = (hue <= 20) & (saturation >= 100) & (value >= 140)
        mask = (lime | orange).astype(np.uint8)
        # Do not borrow evidence from another person's overlapping box.
        for other in workers:
            if other.id == worker.id:
                continue
            a, b, c, d = other.box
            a, b, c, d = (
                max(left, int(a)),
                max(top, int(b)),
                min(right, int(c)),
                min(bottom, int(d)),
            )
            if c > a and d > b:
                mask[b - top : d - top, a - left : c - left] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        coverage = float(mask.mean())
        largest = float(stats[1:, cv2.CC_STAT_AREA].max() / mask.size) if len(stats) > 1 else 0.0
        if coverage >= config.get("min_coverage", 0.10) and largest >= config.get(
            "min_patch_coverage", 0.05
        ):
            state["vest"] = "conflict"
            evidence[worker.id] = {
                "vest": {
                    "reason": "fluorescent_torso_conflicts_with_missing_vest",
                    "model_state": "missing",
                    "decision": "conflict",
                    "coverage": round(coverage, 4),
                    "largest_patch": round(largest, 4),
                }
            }
    return evidence
