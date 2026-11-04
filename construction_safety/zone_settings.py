"""Validated, versioned zone settings shared by the editor and runtime."""

import hashlib
import json
import math
import uuid


def revision(zones):
    """Return a stable content token for detecting concurrent editor changes."""
    return hashlib.sha256(
        json.dumps(zones, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def validate_zones(zones):
    """Validate simple polygons in normalized [0, 1] image coordinates."""
    if not isinstance(zones, list) or len(zones) > 32:
        raise ValueError("Use at most 32 zones")
    names = set()

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on(a, b, c):
        return (
            abs(cross(a, b, c)) < 1e-10
            and min(a[0], b[0]) - 1e-10 <= c[0] <= max(a[0], b[0]) + 1e-10
            and min(a[1], b[1]) - 1e-10 <= c[1] <= max(a[1], b[1]) + 1e-10
        )

    def intersects(a, b, c, d):
        return (
            (cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0)
            or on(a, b, c)
            or on(a, b, d)
            or on(c, d, a)
            or on(c, d, b)
        )

    result = []
    for z in zones:
        if not isinstance(z, dict):
            raise ValueError("Invalid zone")
        name = z.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 64 or name in names:
            raise ValueError("Zone names must be unique, nonempty and at most 64 characters")
        names.add(name)
        level = z.get("level")
        restricted = z.get("restricted", level == 3)
        if type(level) is not int or level not in (0, 1, 2, 3) or type(restricted) is not bool:
            raise ValueError("Invalid zone severity")
        points = z.get("points")
        if not isinstance(points, list) or not 3 <= len(points) <= 32:
            raise ValueError("Each polygon needs 3–32 vertices")
        for p in points:
            if (
                not isinstance(p, list)
                or len(p) != 2
                or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                    for v in p
                )
            ):
                raise ValueError("Vertices must be normalized coordinates between 0 and 1")
        n = len(points)
        if len({tuple(p) for p in points}) != n:
            raise ValueError("Polygon vertices must be distinct")
        area = (
            abs(
                sum(
                    points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
                    for i in range(n)
                )
            )
            / 2
        )
        if area < 0.00001:
            raise ValueError("Polygon area is too small")
        for i in range(n):
            for j in range(i + 1, n):
                if j == i + 1 or (i == 0 and j == n - 1):
                    continue
                if intersects(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n]):
                    raise ValueError("Polygon edges must not cross or touch")
        result.append({"name": name, "level": level, "restricted": restricted, "points": points})
    return result


def save_zones(path, zones, expected_revision):
    """Back up and atomically replace zones if the editor revision is current.

    Callers serialize writes with the dashboard zone lock; this token detects
    stale browser edits, not concurrent filesystem writes by other processes.
    """
    zones = validate_zones(zones)
    config = json.loads(path.read_text())
    if revision(config.get("zones", [])) != expected_revision:
        raise ValueError("Zones changed elsewhere. Reload before saving.")
    old = path.read_text()
    config["zones"] = zones
    version = revision(zones)
    backup = path.parent / "zone-history"
    backup.mkdir(exist_ok=True)
    (backup / f"{path.stem}-{uuid.uuid4().hex}.json").write_text(old)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    tmp.replace(path)
    return version
