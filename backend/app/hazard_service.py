"""Hazard lifecycle engine: observation intake -> position estimate -> severity
-> dedup into a Hazard -> status transitions. The single source of truth for
how a detection becomes (or joins) a tracked hazard. Used by both the worker
(AI intake) and the API (staff/resident intake)."""
from __future__ import annotations
import math
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import entities as _entities  # noqa: F401 — FK targets (users/routes/images)
from app.models.hazards import (
    Hazard, HazardObservation, HazardCategory, HazardStatus, HazardSeverity,
    HazardSource, HazardStatusHistory,
)

_SEV_ORDER = [HazardSeverity.LOW, HazardSeverity.MEDIUM, HazardSeverity.HIGH, HazardSeverity.CRITICAL]
# statuses a new sighting can dedup into (i.e. still a live hazard)
_LIVE = (HazardStatus.SUSPECTED, HazardStatus.PENDING_REVIEW, HazardStatus.OPEN,
         HazardStatus.IN_PROGRESS, HazardStatus.LIKELY_FIXED, HazardStatus.REOPENED)

# Production safety floors. Category settings may be stricter, never looser.
AI_MIN_OBSERVATION_CONFIDENCE = 0.50
AI_MIN_LEANING_CONFIDENCE = 0.65


def meters_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat = (a[0] - b[0]) * 111_320.0
    dlng = (a[1] - b[1]) * 111_320.0 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlng)


def estimate_position(vlat: float, vlng: float, heading_deg: float | None,
                      camera=None) -> tuple[float, float, float]:
    """Project the hazard a few metres from the vehicle. The truck GPS is not the
    hazard: a front camera sees ~6m ahead, a side camera ~4m to the side. Without
    a heading we fall back to the vehicle point with a wider accuracy. Returns
    (lat, lng, accuracy_m)."""
    if heading_deg is None:
        return vlat, vlng, 12.0
    pos = (camera.position if camera else "front")
    dist, bearing_off = {"front": (6.0, 0), "down": (3.0, 0),
                         "left": (4.0, -90), "right": (4.0, 90)}.get(pos, (6.0, 0))
    brg = math.radians((heading_deg + bearing_off) % 360)
    dlat = dist * math.cos(brg) / 111_320.0
    dlng = dist * math.sin(brg) / (111_320.0 * math.cos(math.radians(vlat)))
    return vlat + dlat, vlng + dlng, 8.0


def _band(conf: float, min_conf: float) -> str:
    if conf >= min_conf + 0.08:
        return "high"
    if conf >= min_conf:
        return "medium"
    return "low"


def compute_severity(cat: HazardCategory, *, blocks_path: bool, near_sensitive: bool,
                     is_danger: bool, estimated_size: str | None) -> HazardSeverity:
    """base severity -> +1 level if it blocks a path / sits near a sensitive site
    or is large -> CRITICAL if it's an electrical/fire/fall danger."""
    if is_danger:
        return HazardSeverity.CRITICAL
    lvl = _SEV_ORDER.index(cat.default_severity)
    if blocks_path or near_sensitive or estimated_size == "large":
        lvl = min(lvl + 1, len(_SEV_ORDER) - 1)
    return _SEV_ORDER[lvl]


def _log(db: Session, hz: Hazard, old: str | None, new: str, note: str, user_id=None):
    db.add(HazardStatusHistory(hazard_id=hz.id, old_status=old, new_status=new,
                               note=note, user_id=user_id))


# A moving camera projects one object to scattered positions as the truck
# approaches it, so pure spatial dedup fails. Detections of the same category on
# the same scan that are close in TIME are the same object being driven past —
# merge them even when their estimated positions differ by up to CHAIN_SPATIAL_M.
CHAIN_WINDOW_S = 12.0
CHAIN_SPATIAL_M = 25.0


def find_duplicate(db: Session, category_key: str, lat: float, lng: float,
                   radius_m: float, route_id: int | None = None,
                   captured_at: datetime | None = None) -> Hazard | None:
    """Nearest live hazard of the same category to merge into. Matches either by
    tight spatial proximity (radius_m) OR — for the same scan — by temporal
    proximity within a looser spatial cap, which defeats moving-camera position
    scatter. Village-scale, so a Python scan is fine (no PostGIS needed)."""
    rows = db.scalars(select(Hazard).where(
        Hazard.category_key == category_key,
        Hazard.status.in_(_LIVE),
        Hazard.latitude.is_not(None),
    )).all()
    best, best_d = None, radius_m
    for h in rows:
        d = meters_between((lat, lng), (h.latitude, h.longitude))
        if d < best_d:                       # tight spatial match
            best, best_d = h, d
        elif (route_id is not None and h.route_id == route_id and captured_at is not None
              and h.last_detected_at is not None and d < CHAIN_SPATIAL_M
              and abs((captured_at - h.last_detected_at).total_seconds()) <= CHAIN_WINDOW_S):
            # same object seen moments earlier on this pass, at a scattered position
            if best is None or d < best_d:
                best, best_d = h, d
    return best


def ingest_observation(db: Session, *, category_key: str, confidence: float,
                       vehicle_lat: float | None, vehicle_lng: float | None,
                       heading_deg: float | None = None, camera=None, subtype: str | None = None,
                       bbox: str | None = None, crop_path: str | None = None,
                       annotated_path: str | None = None, route_id: int | None = None,
                       image_id: int | None = None, video_segment_id: int | None = None,
                       detector_name: str = "openvocab", detector_version: str = "owlvit",
                       image_quality: str | None = "ok", quality_flags: str | None = None,
                       blocks_path: bool = False, near_sensitive: bool = False,
                       is_danger: bool = False, estimated_size: str | None = None,
                       source: HazardSource = HazardSource.AI,
                       tilt_degrees: float | None = None, baseline_deg: float | None = None,
                       base_visible: bool | None = None, cables_condition: str | None = None,
                       tilt_axis: str | None = None, severity_override: HazardSeverity | None = None,
                       image_score: float = 0.0,
                       captured_at: datetime | None = None) -> tuple[HazardObservation, Hazard | None]:
    """Turn one detection into a stored observation and fold it into a Hazard.

    Returns (observation, hazard-or-None). A hazard is None when the detection is
    low-confidence or unusable-quality: the observation is still kept as training
    signal, but no active hazard is opened.
    """
    cat = db.scalar(select(HazardCategory).where(HazardCategory.key == category_key))
    configured_min = cat.min_confidence if cat else AI_MIN_OBSERVATION_CONFIDENCE
    if source == HazardSource.AI:
        min_conf = max(configured_min, AI_MIN_LEANING_CONFIDENCE if category_key == "leaning_pole" else AI_MIN_OBSERVATION_CONFIDENCE)
    else:
        min_conf = configured_min
    band = _band(confidence, min_conf)

    lat = lng = acc = None
    if vehicle_lat is not None and vehicle_lng is not None:
        lat, lng, acc = estimate_position(vehicle_lat, vehicle_lng, heading_deg, camera)

    is_leaning = category_key == "leaning_pole"
    severity = severity_override or (compute_severity(
        cat, blocks_path=blocks_path, near_sensitive=near_sensitive,
        is_danger=is_danger, estimated_size=estimated_size,
    ) if cat else HazardSeverity.MEDIUM)

    obs = HazardObservation(
        category_key=category_key, subtype=subtype, confidence=confidence,
        confidence_band=band, severity=severity, bbox=bbox, crop_path=crop_path,
        annotated_path=annotated_path, latitude=lat, longitude=lng,
        location_accuracy_m=acc, vehicle_latitude=vehicle_lat, vehicle_longitude=vehicle_lng,
        heading_deg=heading_deg, route_id=route_id, image_id=image_id,
        video_segment_id=video_segment_id, camera_id=(camera.id if camera else None),
        detector_name=detector_name, detector_version=detector_version,
        image_quality=image_quality, quality_flags=quality_flags,
        tilt_degrees=tilt_degrees, baseline_deg=baseline_deg, base_visible=base_visible,
        cables_condition=cables_condition, tilt_axis=tilt_axis, image_score=image_score,
        captured_at=captured_at,
    )
    db.add(obs)
    db.flush()

    # low confidence or unusable image -> keep only as training signal
    if confidence < min_conf or band == "low" or image_quality == "unusable" or lat is None:
        obs.status = "training_only"
        return obs, None

    now = captured_at or datetime.utcnow()
    radius = cat.dedup_radius_m if cat else 5.0
    hz = find_duplicate(db, category_key, lat, lng, radius, route_id=route_id, captured_at=now)

    if hz is None:
        # Leaning poles are never auto-declared a hazard from one frame — they
        # start SUSPECTED and require field inspection. Others auto-open only
        # when the category allows it AND confidence is high.
        # AI suggestions never directly become OPEN municipal hazards. Human/staff
        # intake may still open according to category policy.
        if is_leaning:
            status = HazardStatus.SUSPECTED
        elif source == HazardSource.AI:
            status = HazardStatus.PENDING_REVIEW
        else:
            auto = (cat.auto_open_allowed if cat else True) and band == "high"
            status = HazardStatus.OPEN if auto else HazardStatus.PENDING_REVIEW
        hz = Hazard(
            category_key=category_key, subtype=subtype, route_id=route_id,
            status=status, severity=severity,
            confidence=confidence, latitude=lat, longitude=lng, location_accuracy_m=acc,
            first_detected_at=now, last_detected_at=now, observation_count=1,
            distinct_scan_days=1, source=source,
            assigned_department=(cat.department if cat else None),
            blocks_path=blocks_path, near_sensitive=near_sensitive, is_danger=is_danger,
            estimated_size=estimated_size, best_observation_id=obs.id,
            tilt_degrees=tilt_degrees, base_visible=base_visible,
            detector_version=detector_version,
        )
        db.add(hz)
        db.flush()
        obs.hazard_id = hz.id
        obs.status = "approved" if status == HazardStatus.OPEN else "pending"
        note = f"{source.value} detection, confidence {confidence:.2f}"
        if is_leaning and tilt_degrees is not None:
            note = f"suspected lean {tilt_degrees}° (base {'visible' if base_visible else 'not visible'})"
        _log(db, hz, None, status.value, note)
        return obs, hz

    # duplicate -> add an observation to the existing hazard
    obs.hazard_id = hz.id
    obs.status = "duplicate"
    prev_status = hz.status
    hz.observation_count += 1
    if now.date() != hz.last_detected_at.date():
        hz.distinct_scan_days += 1
    hz.last_detected_at = now
    hz.missed_scans = 0
    if confidence > hz.confidence:
        hz.confidence = confidence
    # The representative shot + the hazard's map position come from the BEST-quality
    # observation (sharpest, closest, best-framed), not just the most confident one.
    best = db.get(HazardObservation, hz.best_observation_id) if hz.best_observation_id else None
    if best is None or image_score > (best.image_score or 0.0):
        hz.best_observation_id = obs.id
        hz.latitude, hz.longitude, hz.location_accuracy_m = lat, lng, acc
    # severity can only rise from a repeat sighting
    if _SEV_ORDER.index(severity) > _SEV_ORDER.index(hz.severity):
        hz.severity = severity
    # Leaning pole: aggregate valid observations robustly. Never retain the
    # maximum noisy angle as the representative value.
    if is_leaning and tilt_degrees is not None:
        import statistics
        if base_visible and not hz.base_visible:
            hz.base_visible = True
        values = list(db.scalars(select(HazardObservation.tilt_degrees).where(
            HazardObservation.hazard_id == hz.id,
            HazardObservation.tilt_degrees.is_not(None),
            HazardObservation.status != "rejected",
        )).all())
        values = [float(v) for v in values if v is not None]
        old_tilt = hz.tilt_degrees
        if values:
            hz.tilt_degrees = round(float(statistics.median(values)), 1)
            if len(values) >= 5:
                spread = statistics.pstdev(values)
                if spread <= 2.0 and hz.status == HazardStatus.SUSPECTED:
                    hz.status = HazardStatus.PENDING_REVIEW
                    _log(db, hz, prev_status.value, hz.status.value,
                         f"multi-frame tilt confirmed: median {hz.tilt_degrees}°, stddev {spread:.1f}°")
            if old_tilt is not None and hz.tilt_degrees > old_tilt + 2.0:
                hz.tilt_worsening = True
    # a hazard we thought was fixed showing up again -> reopen
    if hz.status == HazardStatus.LIKELY_FIXED:
        hz.status = HazardStatus.REOPENED
        _log(db, hz, prev_status.value, hz.status.value, "seen again after likely-fixed")
    hz.updated_at = now
    return obs, hz
