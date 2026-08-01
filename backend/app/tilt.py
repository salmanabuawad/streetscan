"""Leaning-pole tilt analyzer — a Hazard Detection Engine capability.

Given a frame and a pole/sign/tree bounding box (from the Asset Detection
Engine's electricity/communication sub-engine, or an OWL-ViT hazard detection),
estimate how far the object leans from true vertical.

Key idea for perspective robustness: DON'T measure the pole against the image
edge (the truck/camera may be rolled or the pole may be off-centre, which reads
as tilt under perspective). Instead measure it against the *scene's own vertical
structures* — building edges and other poles — whose length-weighted median
angle gives the apparent-vertical baseline for that frame. A pole that deviates
from what everything else agrees is vertical is genuinely leaning.

Single-frame tilt from an uncalibrated dashcam is inherently noisy, so the
lifecycle keeps these SUSPECTED and requires field inspection; confidence
strengthens across frames/scans (see hazard_service).
"""
from __future__ import annotations
import math

import cv2
import numpy as np

LEANING_KEY = "leaning_pole"

# asset_type that the Asset Detection Engine may emit -> pole subtype we track
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
# per-subtype tilt thresholds (deg): (monitor, suspect, high). Signs/trees are
# more tolerant; a light pole leaning is more alarming than a guy-wired telecom pole.
SUBTYPE_THRESHOLDS = {
    "electric_pole": (3, 5, 10), "utility_pole": (3, 5, 10), "light_pole": (3, 5, 10),
    "telecom_pole": (4, 7, 12), "sign": (5, 10, 20), "tree": (8, 15, 30),
}


def _line_angle_deg(x1, y1, x2, y2) -> float:
    """Angle of a segment from vertical, in degrees (0 = perfectly vertical)."""
    dx, dy = (x2 - x1), (y2 - y1)
    if dy == 0:
        return 90.0
    return math.degrees(math.atan2(dx, -dy))  # image y grows downward


def _near_vertical_lines(gray, max_from_vertical=35.0):
    """HoughP lines that are within `max_from_vertical` of vertical, as
    (angle, length, (x1,y1,x2,y2))."""
    edges = cv2.Canny(gray, 60, 160)
    lines = cv2.HoughLinesP(edges, 1, math.pi / 180, threshold=40,
                            minLineLength=max(20, gray.shape[0] // 6), maxLineGap=12)
    out = []
    if lines is None:
        return out
    for l in np.asarray(lines).reshape(-1, 4):
        x1, y1, x2, y2 = (int(v) for v in l)
        ang = _line_angle_deg(x1, y1, x2, y2)
        if abs(ang) <= max_from_vertical:
            out.append((ang, math.hypot(x2 - x1, y2 - y1), (x1, y1, x2, y2)))
    return out


def _weighted_median_angle(items) -> float | None:
    if not items:
        return None
    items = sorted(items, key=lambda t: t[0])
    total = sum(w for _, w, _ in items)
    acc = 0.0
    for ang, w, _ in items:
        acc += w
        if acc >= total / 2:
            return ang
    return items[-1][0]


def estimate_tilt(frame, bbox) -> dict | None:
    """Estimate a pole's tilt. Returns None when no reliable axis is found.
    `bbox` = (x1,y1,x2,y2) in frame pixels."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in bbox)
    # pad the crop a little so the axis line is well supported
    px, py = int((x2 - x1) * 0.15), int((y2 - y1) * 0.05)
    cx1, cy1 = max(0, x1 - px), max(0, y1 - py)
    cx2, cy2 = min(w, x2 + px), min(h, y2 + py)
    crop = frame[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return None
    cgray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    pole_lines = _near_vertical_lines(cgray, max_from_vertical=40.0)
    if not pole_lines:
        return None
    # the pole axis = the longest near-vertical line in the crop
    pole_lines.sort(key=lambda t: -t[1])
    pole_ang, _len, (ax1, ay1, ax2, ay2) = pole_lines[0]
    # Scene baseline = length-weighted median of OTHER near-vertical structures
    # (buildings, other poles). Exclude the measured pole's own bbox, or it votes
    # for its own "vertical" and the tilt reads as zero. Fall back to image
    # vertical when the scene has too few reference verticals to trust.
    fgray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    scene = []
    for ang, ln, (lx1, ly1, lx2, ly2) in _near_vertical_lines(fgray, 30.0):
        mx, my = (lx1 + lx2) / 2, (ly1 + ly2) / 2
        if x1 <= mx <= x2 and y1 <= my <= y2:      # inside the pole box — skip
            continue
        scene.append((ang, ln, (lx1, ly1, lx2, ly2)))
    baseline = _weighted_median_angle(scene) if len(scene) >= 2 else 0.0
    tilt = round(abs(pole_ang - baseline), 1)

    base_visible = y2 <= h - 6            # bottom of box not cut off by the frame edge
    # cheap cable heuristic: strong near-horizontal edges around the pole top
    top = frame[max(0, y1 - 10):y1 + int((y2 - y1) * 0.25), max(0, x1 - 30):min(w, x2 + 30)]
    cables = None
    if top.size:
        hl = cv2.HoughLinesP(cv2.Canny(cv2.cvtColor(top, cv2.COLOR_BGR2GRAY), 60, 160),
                             1, math.pi / 180, 30, minLineLength=25, maxLineGap=8)
        if hl is not None and any(abs(_line_angle_deg(*l) - 90) < 20
                                  for l in np.asarray(hl).reshape(-1, 4)):
            cables = "present"
    conf_factor = min(1.0, pole_lines[0][1] / max(1, (cy2 - cy1)))  # axis length vs crop height
    # map axis endpoints back to full-frame coords
    axis = [ax1 + cx1, ay1 + cy1, ax2 + cx1, ay2 + cy1]
    return {
        "tilt_degrees": tilt, "baseline_deg": round(baseline, 1),
        "base_visible": base_visible, "cables_condition": cables,
        "axis": axis, "confidence_factor": round(conf_factor, 2),
    }


def classify_tilt(subtype: str, tilt: float, thresholds=None):
    """Return (is_hazard, severity_str) for a measured tilt.
    <monitor: not a hazard. monitor..suspect: low. suspect..high: medium.
    >high: high. (Field inspection always required — see business logic.)"""
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
