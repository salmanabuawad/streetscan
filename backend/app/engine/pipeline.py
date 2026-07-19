"""Default composition of the asset-analysis engine.

Wires replaceable adapters into the production flow:
    detect -> (OCR on sign categories) -> condition -> annotate + crop -> result

Confidence rules and category config come from the DB (CategoryPrompt rows), so
thresholds and active detectors change without code edits.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import cv2

from app.engine.base import (
    AssetAnalysisEngine, AssetAnalysisResult, CaptureContext, CategoryPrompt, DetectedAsset,
)

SIGN_CATEGORIES = {"commercial_sign", "street_name_sign", "institution_sign", "traffic_sign",
                   "public_building_sign"}
MAX_PER_CATEGORY = 3


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a; bx1, by1, bx2, by2 = b
    ix1, iy1, ix2, iy2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def _nms_cap(dets: list[DetectedAsset]) -> list[DetectedAsset]:
    dets = sorted(dets, key=lambda d: -d.confidence)
    kept: list[DetectedAsset] = []
    per_cat: dict[str, int] = {}
    for d in dets:
        if any(_iou(d.box, k.box) >= 0.5 and d.proposed_category == k.proposed_category for k in kept):
            continue
        n = per_cat.get(d.proposed_category, 0)
        if n >= MAX_PER_CATEGORY:
            continue
        per_cat[d.proposed_category] = n + 1
        kept.append(d)
    return kept


class DefaultAssetAnalysisEngine(AssetAnalysisEngine):
    def __init__(self, detector, categories: list[CategoryPrompt], ocr=None, condition=None,
                 crops_dir: str = "uploads/crops", annotated_dir: str = "uploads/annotated"):
        self.detector = detector
        self.categories = categories
        self.ocr = ocr
        self.condition = condition
        self.crops_dir = Path(crops_dir)
        self.annotated_dir = Path(annotated_dir)

    def analyze_image(self, image_path: str, context: CaptureContext,
                      frame=None) -> AssetAnalysisResult:
        """Analyze a still. Callers holding a decoded frame (e.g. video sampling)
        pass it as `frame` and image_path is ignored."""
        t0 = time.time()
        result = AssetAnalysisResult(context=context, model_name=self.detector.name,
                                     model_version=self.detector.version)
        img = frame if frame is not None else cv2.imread(image_path)
        if img is None:
            result.warnings.append("unreadable")
            return result

        dets = _nms_cap(self.detector.detect(img, self.categories))
        annotated = img.copy()
        for d in dets:
            x1, y1, x2, y2 = (int(v) for v in d.box)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(img.shape[1], x2), min(img.shape[0], y2)
            if x2 <= x1 or y2 <= y1:
                continue

            if self.ocr and d.proposed_category in SIGN_CATEGORIES:
                r = self.ocr.read(img, d.box)
                if r:
                    d.ocr_text = r["text"]
                    d.ocr_language = r["language"]
                    d.ocr_confidence = r["confidence"]

            if self.condition:
                d.condition, d.defect = self.condition.assess(img[y1:y2, x1:x2], d)

            cdir = self.crops_dir / d.proposed_category
            cdir.mkdir(parents=True, exist_ok=True)
            crop = cdir / f"{uuid.uuid4().hex}.jpg"
            cv2.imwrite(str(crop), img[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 80])
            d.crop_path = str(crop)

            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 200, 255), 3)
            cv2.putText(annotated, f"{d.proposed_category} {d.confidence}", (x1, max(y1 - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
            result.assets.append(d)

        if result.assets:
            self.annotated_dir.mkdir(parents=True, exist_ok=True)
            apath = self.annotated_dir / f"{uuid.uuid4().hex}.jpg"
            cv2.imwrite(str(apath), annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
            result.annotated_path = str(apath)

        result.processing_ms = int((time.time() - t0) * 1000)
        return result
