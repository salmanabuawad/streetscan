"""Image-quality scoring for best-shot selection.

A roof camera on a moving truck sees each object across many frames — far then
near, sharp then motion-blurred, centred then at the edge. When duplicate
detections of one object collapse into a single record, we keep the frame with
the BEST view, scored here (higher = better):

  sharpness  (Laplacian variance — rejects motion blur)   weight .45
  size       (bbox area fraction — closer/bigger is better) weight .30
  centering  (near frame centre = less lens distortion)     weight .25
  * penalised when the box is cut off by the frame edge (incomplete object)
"""
from __future__ import annotations
import math

import cv2


def image_quality_score(frame, bbox) -> float:
    if frame is None:
        return 0.0
    h, w = frame.shape[:2]
    try:
        x1, y1, x2, y2 = (int(float(v)) for v in bbox)
    except (TypeError, ValueError):
        return 0.0
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    crop = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    sharp_n = min(1.0, cv2.Laplacian(gray, cv2.CV_64F).var() / 300.0)
    area_n = min(1.0, ((x2 - x1) * (y2 - y1) / float(w * h)) / 0.15)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    center_n = max(0.0, 1.0 - math.hypot((cx - w / 2) / w, (cy - h / 2) / h) * 1.6)
    margin = max(2, int(0.004 * max(w, h)))
    cut = x1 <= margin or y1 <= margin or x2 >= w - margin or y2 >= h - margin
    edge_pen = 0.7 if cut else 1.0
    return round((0.45 * sharp_n + 0.30 * area_n + 0.25 * center_n) * edge_pen, 4)
