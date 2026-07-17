"""Pretrained object-detector adapter — YOLO (Ultralytics).

Uses a fixed-class model (COCO by default, or a custom-trained municipal model
via settings.model_path) and maps its classes onto config categories. COCO
covers a handful of our categories directly (hydrant, bench, bus stop, traffic
sign, tree); everything else comes from the open-vocabulary adapter until a
municipal model is trained and registered.
"""
from __future__ import annotations

from app.core.config import settings
from app.engine.base import CategoryPrompt, DetectedAsset

# COCO class name -> (category, layer) for the classes we care about
COCO_MAP = {
    "fire hydrant": ("hydrant", "water"),
    "bench": ("bench", "public_space"),
    "bus": ("bus_stop", "public_space"),
    "stop sign": ("traffic_sign", "road"),
    "potted plant": ("tree", "public_space"),
    "traffic light": ("traffic_sign", "road"),
}


class YoloDetector:
    name = "yolo"

    def __init__(self, model_path: str | None = None, conf: float | None = None):
        self.model_path = model_path or settings.model_path
        self.conf = conf if conf is not None else settings.detection_confidence
        self.version = self.model_path
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)

    def detect(self, image, prompts: list[CategoryPrompt]) -> list[DetectedAsset]:
        self._ensure_loaded()
        wanted = {c.name for c in prompts}
        thresh = {c.name: c.min_confidence for c in prompts}
        out: list[DetectedAsset] = []
        for r in self._model.predict(image, verbose=False, conf=self.conf):
            for b in r.boxes:
                coco = r.names[int(b.cls[0])]
                mapped = COCO_MAP.get(coco)
                # a custom municipal model emits our category names directly
                cat, layer = mapped if mapped else (coco, next((c.infrastructure_layer for c in prompts if c.name == coco), "unknown"))
                if cat not in wanted:
                    continue
                s = float(b.conf[0])
                if s < thresh.get(cat, 0.25):
                    continue
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                out.append(DetectedAsset(
                    proposed_category=cat, infrastructure_layer=layer, confidence=round(s, 3),
                    box=[x1, y1, x2, y2], detector_name=self.name, detector_version=self.version,
                ))
        return out
