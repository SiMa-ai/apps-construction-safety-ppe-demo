"""Conservative incident eligibility from boxes, not a full-body or depth model.

These checks catch clipping, overlapping people and sudden box truncation. An
unmodelled obstruction can still hide a body part inside an otherwise normal box.
"""

import math


class VisibilityPolicy:
    def __init__(self, config, width, height):
        self.width, self.height = width, height
        self.border = self._value(config, "border_margin_fraction", 0.01, 0, 0.1)
        self.min_height = self._value(config, "min_height_fraction", 0.08, 0, 1)
        self.max_ratio = self._value(config, "max_width_height_ratio", 0.9, 0.1, 3)
        self.overlap = self._value(config, "person_overlap_fraction", 0.2, 0.01, 1)
        self.height_ratio = self._value(config, "min_relative_height", 0.65, 0, 1)
        self.min_score = self._value(config, "min_score", 0.5, 0, 1)

    @staticmethod
    def _value(config, key, default, low, high):
        value = float(config.get(key, default))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"visibility.{key} must be between {low} and {high}")
        return value

    def assess(self, workers):
        result = {}
        for worker in workers:
            x1, y1, x2, y2 = worker.box
            width, height = x2 - x1, y2 - y1
            partial, uncertain = [], []
            mx, my = max(2, self.width * self.border), max(2, self.height * self.border)
            edges = [
                name
                for name, clipped in (
                    ("left", x1 <= mx),
                    ("top", y1 <= my),
                    ("right", x2 >= self.width - mx),
                    ("bottom", y2 >= self.height - my),
                )
                if clipped
            ]
            if edges:
                partial.append("frame_edge")
            overlaps = []
            for other in workers:
                if other.id == worker.id:
                    continue
                a, b, c, d = other.box
                intersection = max(0, min(x2, c) - max(x1, a)) * max(0, min(y2, d) - max(y1, b))
                # Boxes cannot determine which person is in front. Defer both
                # people when either box has substantial shared image area.
                smaller = max(1, min(width * height, (c - a) * (d - b)))
                if intersection / smaller >= self.overlap:
                    overlaps.append(other.id)
            if overlaps:
                partial.append("possible_person_occlusion")
            if worker.height_reference and height < worker.height_reference * self.height_ratio:
                partial.append("box_height_drop")
            if height < self.height * self.min_height:
                uncertain.append("too_small")
            if width / max(1, height) > self.max_ratio:
                uncertain.append("body_extent_uncertain")
            if worker.score < self.min_score:
                uncertain.append("low_confidence")
            result[worker.id] = {
                "state": "partial" if partial else "uncertain" if uncertain else "clear",
                "incident_eligible": not (partial or uncertain),
                "reasons": partial + uncertain,
                "frame_edges": edges,
                "overlapping_ids": overlaps,
                "method": "box_heuristics",
                "full_body_verified": False,
            }
        return result
