"""Category-specific validation for AI hazard proposals.

Open-vocabulary detections are suggestions, not municipal findings.  This module
performs conservative scene checks before an observation may enter the hazard
lifecycle.  It intentionally prefers false negatives over false accusations,
especially for construction materials that may be stored legally on private
property.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Sequence

import cv2
import numpy as np


CONSTRUCTION_CATEGORIES = {"construction_debris", "construction_materials"}


@dataclass
class ValidationResult:
    passed: bool
    confidence: float
    status: str = "validated"  # validated | needs_better_view | rejected
    reasons: list[str] = field(default_factory=list)
    blocks_path: bool = False
    estimated_size: str | None = None
    public_space_likelihood: float = 0.0


def _clip_box(box: Sequence[float], width: int, height: int) -> tuple[int, int, int, int] | None:
    if len(box) != 4:
        return None
    x1, y1, x2, y2 = [int(round(float(v))) for v in box]
    x1, x2 = sorted((max(0, x1), min(width, x2)))
    y1, y2 = sorted((max(0, y1), min(height, y2)))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return x1, y1, x2, y2


def _road_public_space_score(box: tuple[int, int, int, int], width: int, height: int) -> float:
    """Weak geometric prior only; never enough by itself to confirm a violation.

    Front-facing survey footage generally places road/sidewalk objects in the
    lower part of the frame.  Objects high in the image are likely roofs, yards,
    façades or detector mistakes.
    """
    x1, y1, x2, y2 = box
    bottom = y2 / max(height, 1)
    center_y = ((y1 + y2) / 2) / max(height, 1)
    center_x = ((x1 + x2) / 2) / max(width, 1)

    score = 0.0
    if bottom >= 0.72:
        score += 0.45
    elif bottom >= 0.60:
        score += 0.25
    if center_y >= 0.58:
        score += 0.30
    elif center_y >= 0.48:
        score += 0.15
    # Extreme side detections are commonly private yards/building fronts.
    if 0.12 <= center_x <= 0.88:
        score += 0.15
    # Contact with the lower frame is useful but may also be dashboard; cap it.
    if bottom >= 0.94:
        score += 0.10
    return min(score, 1.0)


def _material_texture_score(crop: np.ndarray) -> float:
    """Cheap visual support for rubble/sand/blocks.

    This is deliberately only supporting evidence.  It measures neutral/earth
    colours and edge density, but does not claim to determine legality or exact
    material type.
    """
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    valid = v > 30
    if not np.any(valid):
        return 0.0

    neutral = ((s < 75) & (v > 45) & (v < 235) & valid).mean()
    earth = (((h < 35) | (h > 165)) & (s >= 35) & (s < 180) & (v > 35) & valid).mean()
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 70, 160)
    edge_density = float((edges > 0).mean())

    colour_support = min(1.0, float(neutral + earth) / 0.55)
    texture_support = min(1.0, edge_density / 0.16)
    return round(0.6 * colour_support + 0.4 * texture_support, 3)


def validate_construction_detection(frame: np.ndarray, box: Sequence[float],
                                    detector_confidence: float,
                                    category_key: str) -> ValidationResult:
    h, w = frame.shape[:2]
    clipped = _clip_box(box, w, h)
    if clipped is None:
        return ValidationResult(False, 0.0, "rejected", ["invalid_or_tiny_box"])

    x1, y1, x2, y2 = clipped
    bw, bh = x2 - x1, y2 - y1
    area_ratio = (bw * bh) / float(max(w * h, 1))
    reasons: list[str] = []

    if area_ratio < 0.0025:
        return ValidationResult(False, 0.0, "rejected", ["object_too_small"])
    if area_ratio > 0.48:
        return ValidationResult(False, 0.0, "rejected", ["box_too_large_or_scene_level"])
    if y2 / h < 0.52:
        return ValidationResult(False, 0.0, "rejected", ["object_not_on_ground_region"])

    crop = frame[y1:y2, x1:x2]
    public_score = _road_public_space_score(clipped, w, h)
    texture_score = _material_texture_score(crop)

    # Open-vocabulary score is only one component.  The validation confidence is
    # intentionally conservative and bounded by the detector score.
    scene_support = 0.58 * public_score + 0.42 * texture_score
    validation_confidence = min(float(detector_confidence), float(scene_support))

    if public_score < 0.55:
        reasons.append("public_space_not_established")
    if texture_score < 0.35:
        reasons.append("insufficient_material_visual_evidence")
    if detector_confidence < 0.60:
        reasons.append("detector_confidence_below_construction_floor")

    # A single image cannot establish that material is abandoned, illegal, or
    # stored for a long period.  Passing here means only 'reviewable candidate'.
    passed = not reasons
    status = "validated" if passed else "needs_better_view"

    estimated_size = "large" if area_ratio >= 0.12 else "medium" if area_ratio >= 0.035 else "small"
    # Conservative geometric proxy.  Staff still decides actual obstruction.
    bottom = y2 / h
    center_x = ((x1 + x2) / 2) / w
    blocks_path = bool(bottom > 0.82 and 0.22 < center_x < 0.78 and area_ratio > 0.025)

    return ValidationResult(
        passed=passed,
        confidence=round(validation_confidence, 3),
        status=status,
        reasons=reasons,
        blocks_path=blocks_path,
        estimated_size=estimated_size,
        public_space_likelihood=round(public_score, 3),
    )


def validate_hazard_detection(frame: np.ndarray, box: Sequence[float],
                              detector_confidence: float,
                              category_key: str) -> ValidationResult:
    if category_key in CONSTRUCTION_CATEGORIES:
        return validate_construction_detection(frame, box, detector_confidence, category_key)
    return ValidationResult(True, float(detector_confidence))
