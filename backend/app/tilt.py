"""Conservative leaning-pole geometry analysis.

This module intentionally prefers "no reliable measurement" over a false angle.
It does not treat the longest Hough line as a pole axis. Without a segmentation
mask, it requires two long, parallel pole edges and fits their midpoint axis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

LEANING_KEY = "leaning_pole"

POLE_SUBTYPES = {
    "electricity_pole": "electric_pole", "electric_pole": "electric_pole",
    "utility_pole": "utility_pole", "telecom_pole": "telecom_pole",
    "street_light": "light_pole", "damaged_sign": "sign", "sign": "sign",
    "leaning_tree": "tree", "tree": "tree",
}
SUBTYPE_HE = {
    "electric_pole": "עמוד חשמל", "utility_pole": "עמוד תשתית", "telecom_pole": "עמוד תקשורת",
    "light_pole": "עמוד תאורה", "sign": "תמרור", "tree": "עץ נוטה",
}
SUBTYPE_THRESHOLDS = {
    "electric_pole": (3, 5, 10), "utility_pole": (3, 5, 10), "light_pole": (3, 5, 10),
    "telecom_pole": (4, 7, 12), "sign": (5, 10, 20), "tree": (8, 15, 30),
}


@dataclass(frozen=True)
class _Line:
    angle: float
    length: float
    coords: tuple[int, int, int, int]


def _line_angle_deg(x1: int, y1: int, x2: int, y2: int) -> float:
    """Signed angle from image vertical. 0 is vertical; +/-90 is horizontal."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return 0.0
    return math.degrees(math.atan2(dx, abs(dy) + 1e-9))


def _hough_lines(gray: np.ndarray, max_from_vertical: float, min_length: int) -> list[_Line]:
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 60, 160)
    raw = cv2.HoughLinesP(
        edges, 1, math.pi / 180, threshold=max(24, min_length // 3),
        minLineLength=min_length, maxLineGap=max(8, min_length // 10),
    )
    result: list[_Line] = []
    if raw is None:
        return result
    for row in np.asarray(raw).reshape(-1, 4):
        x1, y1, x2, y2 = (int(v) for v in row)
        angle = _line_angle_deg(x1, y1, x2, y2)
        if abs(angle) <= max_from_vertical:
            result.append(_Line(angle, math.hypot(x2 - x1, y2 - y1), (x1, y1, x2, y2)))
    return result


def _x_at_y(line: _Line, y: float) -> float | None:
    x1, y1, x2, y2 = line.coords
    if abs(y2 - y1) < 1e-6:
        return None
    t = (y - y1) / (y2 - y1)
    return x1 + t * (x2 - x1)


def _y_overlap(a: _Line, b: _Line) -> tuple[float, float, float]:
    ay1, ay2 = sorted((a.coords[1], a.coords[3]))
    by1, by2 = sorted((b.coords[1], b.coords[3]))
    lo, hi = max(ay1, by1), min(ay2, by2)
    return lo, hi, max(0.0, hi - lo)


def _find_parallel_edges(lines: list[_Line], crop_w: int, crop_h: int):
    """Find two long parallel edges plausibly belonging to one pole body."""
    best = None
    min_overlap = crop_h * 0.45
    min_sep = max(3.0, crop_w * 0.015)
    max_sep = crop_w * 0.45
    for i, left in enumerate(lines):
        for right in lines[i + 1:]:
            if abs(left.angle - right.angle) > 4.0:
                continue
            lo, hi, overlap = _y_overlap(left, right)
            if overlap < min_overlap:
                continue
            mid_y = (lo + hi) / 2
            lx, rx = _x_at_y(left, mid_y), _x_at_y(right, mid_y)
            if lx is None or rx is None:
                continue
            sep = abs(rx - lx)
            if not (min_sep <= sep <= max_sep):
                continue
            # Prefer long overlap, close angle agreement and a narrow pole body.
            score = (overlap / crop_h) * 0.65 + (1 - abs(left.angle - right.angle) / 4) * 0.2 + (1 - sep / max_sep) * 0.15
            if best is None or score > best[0]:
                best = (score, left, right, lo, hi, sep)
    return best


def _scene_vertical(frame: np.ndarray, excluded_bbox: tuple[int, int, int, int]) -> tuple[float | None, float]:
    """Estimate scene vertical conservatively from multiple long structural lines."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, _ = gray.shape
    lines = _hough_lines(gray, max_from_vertical=15.0, min_length=max(40, h // 5))
    x1, y1, x2, y2 = excluded_bbox
    usable = []
    for line in lines:
        lx1, ly1, lx2, ly2 = line.coords
        mx, my = (lx1 + lx2) / 2, (ly1 + ly2) / 2
        if x1 <= mx <= x2 and y1 <= my <= y2:
            continue
        usable.append(line)
    if len(usable) < 3:
        return None, 0.0
    angles = np.asarray([line.angle for line in usable], dtype=float)
    median = float(np.median(angles))
    mad = float(np.median(np.abs(angles - median)))
    confidence = max(0.0, min(1.0, (len(usable) / 8.0) * (1.0 - min(mad, 8.0) / 8.0)))
    if confidence < 0.45:
        return None, confidence
    return median, confidence


def estimate_tilt(frame: np.ndarray, bbox) -> dict:
    """Return a conservative geometry result.

    `angle_is_valid` is false unless two pole-like parallel edges and a reliable
    scene vertical are available. Callers must never display or persist an angle
    when `angle_is_valid` is false.
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    x1, x2 = sorted((max(0, x1), min(w - 1, x2)))
    y1, y2 = sorted((max(0, y1), min(h - 1, y2)))
    bw, bh = x2 - x1, y2 - y1
    reasons: list[str] = []

    result = {
        "tilt_degrees": None, "corrected_tilt_degrees": None,
        "raw_axis_angle_degrees": None, "baseline_deg": None,
        "angle_is_valid": False, "base_visible": False,
        "base_occluded": True, "occlusion_reason": "not_verified",
        "cables_condition": None, "axis": None,
        "geometry_confidence": 0.0, "confidence_factor": 0.0,
        "rejection_reasons": reasons,
    }
    if bw < 8 or bh < 40:
        reasons.append("bounding_box_too_small")
        return result
    if bw > w * 0.45:
        reasons.append("bounding_box_too_wide")
        return result
    if bh / max(bw, 1) < 1.35:
        reasons.append("bounding_box_not_pole_shaped")
        return result

    # Analyze only inside the detector box; no padding that invites cables/buildings.
    crop = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    lines = _hough_lines(gray, max_from_vertical=35.0, min_length=max(25, int(bh * 0.35)))
    pair = _find_parallel_edges(lines, bw, bh)
    if pair is None:
        reasons.append("no_valid_parallel_pole_edges")
        return result

    pair_score, edge1, edge2, oy1, oy2, sep = pair
    top_y, bottom_y = float(oy1), float(oy2)
    e1_top, e2_top = _x_at_y(edge1, top_y), _x_at_y(edge2, top_y)
    e1_bottom, e2_bottom = _x_at_y(edge1, bottom_y), _x_at_y(edge2, bottom_y)
    if None in (e1_top, e2_top, e1_bottom, e2_bottom):
        reasons.append("axis_interpolation_failed")
        return result
    top_x = (e1_top + e2_top) / 2
    bottom_x = (e1_bottom + e2_bottom) / 2
    raw_angle = _line_angle_deg(int(top_x), int(top_y), int(bottom_x), int(bottom_y))

    baseline, baseline_conf = _scene_vertical(frame, (x1, y1, x2, y2))
    if baseline is None:
        reasons.append("no_reliable_vertical_reference")
        return result

    corrected = abs(raw_angle - baseline)
    visible_ratio = (bottom_y - top_y) / max(1.0, bh)
    geometry_conf = min(1.0, pair_score * 0.65 + baseline_conf * 0.25 + min(1.0, visible_ratio) * 0.10)

    # Conservative base visibility: a bbox coordinate cannot prove ground contact.
    # Until segmentation/ground-contact validation exists, keep it false.
    base_visible = False
    reasons.append("pole_ground_connection_not_verified")

    angle_valid = geometry_conf >= 0.62 and visible_ratio >= 0.45 and corrected <= 35.0
    if not angle_valid:
        reasons.append("geometry_confidence_too_low")
        return result

    axis = [int(round(top_x + x1)), int(round(top_y + y1)),
            int(round(bottom_x + x1)), int(round(bottom_y + y1))]
    result.update({
        "tilt_degrees": round(corrected, 1),
        "corrected_tilt_degrees": round(corrected, 1),
        "raw_axis_angle_degrees": round(raw_angle, 1),
        "baseline_deg": round(baseline, 1),
        "angle_is_valid": True,
        "base_visible": base_visible,
        "base_occluded": True,
        "occlusion_reason": "pole_ground_connection_not_verified",
        "axis": axis,
        "geometry_confidence": round(geometry_conf, 3),
        "confidence_factor": round(geometry_conf, 3),
    })
    return result


def classify_tilt(subtype: str, tilt: float | None, thresholds=None):
    if tilt is None:
        return False, None
    mon, sus, high = thresholds or SUBTYPE_THRESHOLDS.get(subtype, (3, 5, 10))
    if tilt < mon:
        return False, None
    if tilt < sus:
        return True, "low"
    if tilt < high:
        return True, "medium"
    return True, "high"


def recommended_action(base_visible: bool) -> str:
    return "Field inspection required" if not base_visible else "Field inspection to verify base and risk"
