"""Adapt PyTorch and compiled Neat models to source-pixel Detection records.

Both backends accept OpenCV BGR frames. Tracking and incident policy live in
analytics; the paired detectors only control when fresh PPE evidence is available.
"""

import time
from pathlib import Path

import numpy as np

from .analytics import Detection


def tracking_config(config):
    """Keep weak person boxes for association; they cannot start new worker IDs.

    The tracker and visibility policy retain their own higher confidence thresholds
    for creating IDs and assessing incidents, respectively.
    """
    result = dict(config)
    threshold = float(config.get("tracking_score", 0.25))
    if not 0 <= threshold <= 1:
        raise ValueError("tracking_score must be between 0 and 1")
    result["score"] = min(config.get("score", 0.35), threshold)
    result["score_by_category"] = {**config.get("score_by_category", {}), "worker": threshold}
    return result


class YoloDetector:
    """Run a local checkpoint and map its training labels to demo categories."""

    def __init__(self, model_path, config, device="cpu"):
        import torch
        from ultralytics import YOLO

        torch.set_num_threads(config.get("threads", 4))
        if not Path(model_path).is_file():
            raise FileNotFoundError(
                f"Model not found: {model_path}; provide the checkpoint configured for this detector"
            )
        self.model = YOLO(str(model_path))
        self.config, self.device = config, device
        self.categories = config["label_categories"]
        actual = set(self.model.names.values())
        if not actual.intersection(self.categories):
            raise ValueError("Model has no configured detection classes; check label_categories.")
        self.labels = actual

    def detect(self, frame):
        prediction = self.model.predict(
            frame,
            device=self.device,
            imgsz=self.config.get("image_size", 640),
            conf=self.config.get("score", 0.2),
            classes=[i for i, n in self.model.names.items() if n in self.categories],
            iou=0.5,
            agnostic_nms=self.config.get("agnostic_nms", False),
            verbose=False,
        )[0]
        detections = []
        for row in prediction.boxes.data.cpu().numpy():
            x1, y1, x2, y2, score, cls = row[:6]
            label = prediction.names[int(cls)]
            category = self.categories.get(label)
            if (
                category in ("worker", "machine", "ppe")
                and x2 > x1
                and y2 > y1
                and score >= self.config.get("score_by_category", {}).get(category, 0)
            ):
                if category == "worker":
                    limits = self.config.get("worker_box_filter", {})
                    if (y2 - y1) / frame.shape[0] < limits.get("min_height_fraction", 0.0) or (
                        x2 - x1
                    ) / frame.shape[1] > limits.get("max_width_fraction", 1.0):
                        continue
                detections.append(
                    Detection(tuple(map(float, (x1, y1, x2, y2))), float(score), label, category)
                )
        return detections


class NeatDetector:
    """Compiled YOLOv8/v11 or YOLO26 packages with an explicit training class map."""

    def __init__(self, model_path, config, width, height):
        import pyneat

        if not Path(model_path).is_file():
            raise FileNotFoundError(model_path)
        self.pyneat, self.config = pyneat, config
        self.class_map = config["class_map"]
        self.labels = {v["label"] for v in self.class_map.values()}
        opt = pyneat.ModelOptions()
        opt.preprocess.kind = pyneat.InputKind.Image
        opt.preprocess.enable = pyneat.AutoFlag.On
        opt.preprocess.color_convert.input_format = pyneat.PreprocessColorFormat.BGR
        opt.preprocess.preset = pyneat.NormalizePreset.COCO_YOLO
        opt.preprocess.input_max_width = width
        opt.preprocess.input_max_height = height
        opt.preprocess.input_max_depth = 3
        family = config.get("decode_type", "yolov8")
        if family == "yolo26":
            opt.decode_type = pyneat.BoxDecodeType.YoloV26
            # Let the packaged contract select the YOLO26 head layout.
        elif family == "yolov8":
            opt.decode_type = pyneat.BoxDecodeType.YoloV8
            opt.decode_type_option = (
                pyneat.BoxDecodeTypeOption.GroupedByRoleProbability
                if config.get("score_domain") == "prob"
                else pyneat.BoxDecodeTypeOption.GroupedByRoleLogit
            )
        else:
            raise ValueError(f"Unsupported Neat decoder: {family}")
        opt.num_classes = config["num_classes"]
        opt.score_threshold = config.get("score", 0.35)
        opt.nms_iou_threshold = 0.5
        opt.top_k = 100
        # Decode back into source pixels so tracking and polygons share coordinates.
        opt.boxdecode_original_width = width
        opt.boxdecode_original_height = height
        opt.boxdecode_resize_mode = pyneat.ResizeMode.Letterbox
        self.model = pyneat.Model(str(model_path), opt)

    def detect(self, frame):
        p = self.pyneat
        tensor = p.Tensor.from_numpy(
            np.ascontiguousarray(frame),
            copy=True,
            image_format=p.PixelFormat.BGR,
            memory=p.TensorMemory.EV74,
        )
        outputs = self.model.run([tensor], timeout_ms=self.config.get("timeout_ms", 2000))
        if not outputs:
            raise RuntimeError(
                "Neat returned no result (timeout or runtime error); not treating it as an empty scene"
            )
        boxes = p.decode_bbox(outputs)[0].to_numpy()
        h, w = frame.shape[:2]
        result = []
        for x1, y1, x2, y2, score, cls in boxes:
            cls = int(cls)
            definition = self.class_map.get(str(cls))
            if not definition or score < self.config.get("score", 0.35):
                continue
            definition_threshold = self.config.get("score_by_category", {}).get(
                definition["category"], 0
            )
            if score < definition_threshold:
                continue
            box = (
                max(0, float(x1)),
                max(0, float(y1)),
                min(w - 1, float(x2)),
                min(h - 1, float(y2)),
            )
            if box[2] > box[0] and box[3] > box[1]:
                result.append(
                    Detection(box, float(score), definition["label"], definition["category"])
                )
        return result


class Yolo26PPETrackerDetector:
    """YOLO26 people each frame; construction/PPE evidence on sampled frames only."""

    def __init__(self, model_path, config, device, root):
        self.primary = YoloDetector(model_path, tracking_config(config["detector"]), device)
        auxiliary = config["auxiliary_detector"]
        self.auxiliary = YoloDetector(root / auxiliary["model"], auxiliary, device)
        self._configure_sampling(auxiliary)

    def _configure_sampling(self, auxiliary):
        """Require negative PPE labels; absent positive detections are not violations."""
        if not {"NO-Hardhat", "NO-Safety Vest"} <= self.auxiliary.labels:
            raise ValueError(
                "PPE checkpoint lacks the explicit missing-hardhat/vest classes required for incidents"
            )
        self.interval = int(auxiliary.get("every_n_frames", 3))
        if self.interval < 1:
            raise ValueError("every_n_frames must be positive")
        self.index = 0
        self.ppe_updated = False
        self.labels = self.primary.labels | self.auxiliary.labels

    def detect(self, frame):
        begin = time.monotonic()
        result = self.primary.detect(frame)
        self.last_timings = {"people_detector": time.monotonic() - begin}
        # An unsampled frame is not an observation that PPE has disappeared.
        self.ppe_updated = self.index % self.interval == 0
        self.index += 1
        if self.ppe_updated:
            # Only the primary detector supplies people, avoiding duplicate worker tracks.
            begin = time.monotonic()
            result.extend(d for d in self.auxiliary.detect(frame) if d.category != "worker")
            self.last_timings["ppe_detector"] = time.monotonic() - begin
        return result


class NeatYolo26PPETrackerDetector(Yolo26PPETrackerDetector):
    """Compiled YOLO26 people plus sampled compiled PPE; no PyTorch inference."""

    def __init__(self, model_path, config, width, height, root):
        auxiliary = config["auxiliary_detector"]
        # Validate both paths and sampling before allocating accelerator resources.
        if int(auxiliary.get("every_n_frames", 3)) < 1:
            raise ValueError("every_n_frames must be positive")
        if not (root / auxiliary["model"]).is_file():
            raise FileNotFoundError(root / auxiliary["model"])
        self.primary = NeatDetector(model_path, tracking_config(config["detector"]), width, height)
        self.auxiliary = NeatDetector(root / auxiliary["model"], auxiliary, width, height)
        self._configure_sampling(auxiliary)
