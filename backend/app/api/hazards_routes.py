"""Road-hazard API: category config, the AI-review queue, hazard lifecycle
(approve/reject/assign/status/merge), map data, history and the model
dashboard. Mounted under the same /api prefix as the asset routes."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import require_role, get_current_user
from app.models.entities import CapturedImage, Route, VideoSegment
from app import hazard_service as svc
from app.models.hazards import (
    Hazard, HazardObservation, HazardCategory, HazardStatus, HazardSeverity,
    HazardSource, HazardStatusHistory, HazardAssignment, HazardFeedback, AuditLog,
)

router = APIRouter(prefix="/hazards", tags=["hazards"])

DRIVER = Depends(require_role("driver"))
VALIDATOR = Depends(require_role("validator"))
ADMIN = Depends(require_role("admin"))

# lifecycle transitions a staff member may set directly
_STAFF_STATUS = {
    "open": HazardStatus.OPEN, "in_progress": HazardStatus.IN_PROGRESS,
    "likely_fixed": HazardStatus.LIKELY_FIXED, "closed": HazardStatus.CLOSED,
    "reopened": HazardStatus.REOPENED, "rejected": HazardStatus.REJECTED,
}


def _audit(db, user, action, entity, entity_id, detail=""):
    db.add(AuditLog(user_id=getattr(user, "id", None), action=action,
                    entity=entity, entity_id=entity_id, detail=detail))


def _cat_map(db) -> dict[str, HazardCategory]:
    return {c.key: c for c in db.scalars(select(HazardCategory)).all()}


def _hazard_dict(h: Hazard, cats: dict) -> dict:
    c = cats.get(h.category_key)
    return {
        "id": h.id, "category_key": h.category_key,
        "category_he": c.name_he if c else h.category_key,
        "group": c.group if c else None, "color": c.color if c else None,
        "icon": c.icon if c else None,
        "subtype": h.subtype, "status": h.status.value, "severity": h.severity.value,
        "confidence": round(h.confidence, 3),
        "lat": h.latitude, "lng": h.longitude, "location_accuracy_m": h.location_accuracy_m,
        "street_name": h.street_name, "department": h.assigned_department,
        "assigned_user_id": h.assigned_user_id, "source": h.source.value,
        "observation_count": h.observation_count, "distinct_scan_days": h.distinct_scan_days,
        "first_detected_at": h.first_detected_at.isoformat(),
        "last_detected_at": h.last_detected_at.isoformat(),
        "age_days": (datetime.utcnow() - h.first_detected_at).days,
        "estimated_size": h.estimated_size, "blocks_path": h.blocks_path,
        "near_sensitive": h.near_sensitive, "is_danger": h.is_danger,
        "tilt_degrees": h.tilt_degrees, "base_visible": h.base_visible,
        "tilt_worsening": h.tilt_worsening,
        "duplicate_of": h.duplicate_of, "best_observation_id": h.best_observation_id,
    }


# ---------- config ----------
@router.get("/categories", dependencies=[DRIVER])
def list_categories(db: Session = Depends(get_db)):
    return [{
        "key": c.key, "name_he": c.name_he, "name_ar": c.name_ar, "name_en": c.name_en,
        "group": c.group, "detector": c.active_detector, "min_confidence": c.min_confidence,
        "auto_open_allowed": c.auto_open_allowed, "default_severity": c.default_severity.value,
        "dedup_radius_m": c.dedup_radius_m, "department": c.department,
        "icon": c.icon, "color": c.color, "is_mvp": c.is_mvp, "active": c.active,
    } for c in db.scalars(select(HazardCategory).order_by(HazardCategory.group, HazardCategory.key)).all()]


# ---------- AI review queue ----------
@router.get("/review", dependencies=[DRIVER])
def review_queue(limit: int = 200, db: Session = Depends(get_db)):
    """Observations awaiting staff judgement (the 'בדיקת זיהויי AI' screen)."""
    cats = _cat_map(db)
    obs = db.scalars(
        select(HazardObservation).where(HazardObservation.status == "pending")
        .order_by(HazardObservation.confidence.desc()).limit(limit)
    ).all()
    out = []
    for o in obs:
        c = cats.get(o.category_key)
        out.append({
            "id": o.id, "hazard_id": o.hazard_id, "category_key": o.category_key,
            "category_he": c.name_he if c else o.category_key,
            "color": c.color if c else None, "confidence": round(o.confidence, 3),
            "band": o.confidence_band, "severity": o.severity.value if o.severity else None,
            "lat": o.latitude, "lng": o.longitude, "location_accuracy_m": o.location_accuracy_m,
            "image_quality": o.image_quality, "quality_flags": o.quality_flags,
            "detector": o.detector_name, "camera_id": o.camera_id, "subtype": o.subtype,
            "captured_at": o.captured_at.isoformat() if o.captured_at else None,
            "has_image": bool(o.annotated_path or o.crop_path),
            "tilt_degrees": o.tilt_degrees, "baseline_deg": o.baseline_deg,
            "base_visible": o.base_visible, "cables_condition": o.cables_condition,
            "bbox": o.bbox, "tilt_axis": o.tilt_axis,
        })
    return {"count": len(out), "items": out}


@router.get("/observations/{obs_id}/image", dependencies=[DRIVER])
def observation_image(obs_id: int, db: Session = Depends(get_db)):
    o = db.get(HazardObservation, obs_id)
    if not o:
        raise HTTPException(404, "observation not found")
    path = o.annotated_path or o.crop_path
    if not path or not Path(path).is_file():
        raise HTTPException(404, "image file missing")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/observations/{obs_id}/approve", dependencies=[VALIDATOR])
def approve_observation(obs_id: int, body: dict = Body(default={}),
                        user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Approve a detection -> its hazard becomes OPEN. Optional category/severity
    correction is captured as training feedback."""
    o = db.get(HazardObservation, obs_id)
    if not o:
        raise HTTPException(404, "observation not found")
    new_cat = body.get("category_key")
    new_sev = body.get("severity")
    if new_cat and new_cat != o.category_key:
        db.add(HazardFeedback(observation_id=o.id, hazard_id=o.hazard_id,
                              feedback_type="correct_category", corrected_category=new_cat,
                              detector_version=o.detector_version, user_id=user.id))
        o.category_key = new_cat
    o.status = "approved"
    db.add(HazardFeedback(observation_id=o.id, hazard_id=o.hazard_id, feedback_type="confirm",
                          detector_version=o.detector_version, user_id=user.id))
    hz = db.get(Hazard, o.hazard_id) if o.hazard_id else None
    if hz:
        if new_cat:
            hz.category_key = new_cat
        if new_sev:
            try:
                hz.severity = HazardSeverity(new_sev)
            except ValueError:
                pass
        old = hz.status.value
        if hz.status == HazardStatus.PENDING_REVIEW:
            hz.status = HazardStatus.OPEN
            db.add(HazardStatusHistory(hazard_id=hz.id, old_status=old, new_status="open",
                                       note="approved by staff", user_id=user.id))
        hz.updated_at = datetime.utcnow()
    _audit(db, user, "update", "observation", o.id, "approve")
    db.commit()
    return {"ok": True, "hazard_id": o.hazard_id}


@router.post("/observations/{obs_id}/reject", dependencies=[VALIDATOR])
def reject_observation(obs_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Mark a detection a false positive. If its hazard has no other approved
    observation, the hazard is rejected too."""
    o = db.get(HazardObservation, obs_id)
    if not o:
        raise HTTPException(404, "observation not found")
    o.status = "rejected"
    db.add(HazardFeedback(observation_id=o.id, hazard_id=o.hazard_id, feedback_type="false_positive",
                          detector_version=o.detector_version, user_id=user.id))
    hz = db.get(Hazard, o.hazard_id) if o.hazard_id else None
    if hz:
        others = db.scalar(select(func.count(HazardObservation.id)).where(
            HazardObservation.hazard_id == hz.id,
            HazardObservation.status.in_(("approved", "pending", "duplicate")),
            HazardObservation.id != o.id,
        ))
        if not others and hz.status in (HazardStatus.PENDING_REVIEW, HazardStatus.OPEN):
            old = hz.status.value
            hz.status = HazardStatus.REJECTED
            db.add(HazardStatusHistory(hazard_id=hz.id, old_status=old, new_status="rejected",
                                       note="false positive", user_id=user.id))
    _audit(db, user, "update", "observation", o.id, "reject")
    db.commit()
    return {"ok": True}


# ---------- hazard lifecycle ----------
@router.get("", dependencies=[DRIVER])
def list_hazards(status: str | None = None, severity: str | None = None,
                 category: str | None = None, department: str | None = None,
                 source: str | None = None, limit: int = 500, db: Session = Depends(get_db)):
    stmt = select(Hazard)
    if status:
        stmt = stmt.where(Hazard.status == HazardStatus(status))
    if severity:
        stmt = stmt.where(Hazard.severity == HazardSeverity(severity))
    if category:
        stmt = stmt.where(Hazard.category_key == category)
    if department:
        stmt = stmt.where(Hazard.assigned_department == department)
    if source:
        stmt = stmt.where(Hazard.source == HazardSource(source))
    stmt = stmt.order_by(Hazard.last_detected_at.desc()).limit(limit)
    cats = _cat_map(db)
    return [_hazard_dict(h, cats) for h in db.scalars(stmt).all()]


@router.get("/map", dependencies=[DRIVER])
def hazard_map(db: Session = Depends(get_db)):
    """Located hazards for the GIS map (severity colour + category icon)."""
    cats = _cat_map(db)
    rows = db.scalars(select(Hazard).where(
        Hazard.latitude.is_not(None),
        Hazard.status.notin_((HazardStatus.REJECTED,)),
    )).all()
    return {"hazards": [_hazard_dict(h, cats) for h in rows]}


@router.get("/dashboard", dependencies=[DRIVER])
def dashboard(db: Session = Depends(get_db)):
    """Model + operations metrics for the manager view."""
    total = db.scalar(select(func.count(Hazard.id))) or 0
    obs_total = db.scalar(select(func.count(HazardObservation.id))) or 0
    approved = db.scalar(select(func.count(HazardObservation.id)).where(HazardObservation.status == "approved")) or 0
    rejected = db.scalar(select(func.count(HazardObservation.id)).where(HazardObservation.status == "rejected")) or 0
    reviewed = approved + rejected
    by_status = dict(db.execute(select(Hazard.status, func.count(Hazard.id)).group_by(Hazard.status)).all())
    by_severity = dict(db.execute(select(Hazard.severity, func.count(Hazard.id)).group_by(Hazard.severity)).all())
    by_category = dict(db.execute(select(Hazard.category_key, func.count(Hazard.id)).group_by(Hazard.category_key)).all())
    # per-category precision proxy = approved / (approved+rejected) among reviewed observations
    prec = {}
    rows = db.execute(select(HazardObservation.category_key, HazardObservation.status,
                             func.count(HazardObservation.id))
                      .where(HazardObservation.status.in_(("approved", "rejected")))
                      .group_by(HazardObservation.category_key, HazardObservation.status)).all()
    agg: dict[str, list[int]] = {}
    for cat, st, n in rows:
        a = agg.setdefault(cat, [0, 0])
        a[0 if st == "approved" else 1] += n
    for cat, (ap, rj) in agg.items():
        prec[cat] = round(ap / (ap + rj), 3) if (ap + rj) else None
    return {
        "hazards_total": total, "observations_total": obs_total,
        "reviewed": reviewed, "approved": approved, "false_positives": rejected,
        "overall_precision": round(approved / reviewed, 3) if reviewed else None,
        "by_status": {k.value: v for k, v in by_status.items()},
        "by_severity": {k.value: v for k, v in by_severity.items()},
        "by_category": by_category,
        "precision_by_category": prec,
    }


@router.get("/{hazard_id}", dependencies=[DRIVER])
def hazard_detail(hazard_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    h = db.get(Hazard, hazard_id)
    if not h:
        raise HTTPException(404, "hazard not found")
    cats = _cat_map(db)
    obs = db.scalars(select(HazardObservation).where(HazardObservation.hazard_id == hazard_id)
                     .order_by(HazardObservation.created_at)).all()
    hist = db.scalars(select(HazardStatusHistory).where(HazardStatusHistory.hazard_id == hazard_id)
                      .order_by(HazardStatusHistory.created_at)).all()
    _audit(db, user, "view", "hazard", hazard_id)
    db.commit()
    d = _hazard_dict(h, cats)
    d["observations"] = [{
        "id": o.id, "confidence": round(o.confidence, 3), "band": o.confidence_band,
        "status": o.status, "severity": o.severity.value if o.severity else None,
        "captured_at": o.captured_at.isoformat() if o.captured_at else None,
        "quality_flags": o.quality_flags, "has_image": bool(o.annotated_path or o.crop_path),
        "lat": o.latitude, "lng": o.longitude,
        "tilt_degrees": o.tilt_degrees, "baseline_deg": o.baseline_deg,
        "base_visible": o.base_visible, "cables_condition": o.cables_condition,
        "bbox": o.bbox, "tilt_axis": o.tilt_axis,
    } for o in obs]
    d["history"] = [{
        "old": s.old_status, "new": s.new_status, "note": s.note,
        "at": s.created_at.isoformat(), "user_id": s.user_id,
    } for s in hist]
    return d


@router.post("/{hazard_id}/status", dependencies=[VALIDATOR])
def set_status(hazard_id: int, body: dict = Body(...), user=Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Move a hazard through its lifecycle. Closing requires either two clean
    scans (missed_scans>=2) or an explicit staff override (force=true)."""
    h = db.get(Hazard, hazard_id)
    if not h:
        raise HTTPException(404, "hazard not found")
    key = body.get("status")
    if key not in _STAFF_STATUS:
        raise HTTPException(400, f"invalid status; one of {list(_STAFF_STATUS)}")
    new = _STAFF_STATUS[key]
    if new == HazardStatus.CLOSED and h.missed_scans < 2 and not body.get("force"):
        raise HTTPException(409, "close needs 2 clean scans or force=true (avoid closing on a single miss)")
    old = h.status.value
    h.status = new
    h.updated_at = datetime.utcnow()
    db.add(HazardStatusHistory(hazard_id=h.id, old_status=old, new_status=new.value,
                               note=body.get("note", ""), user_id=user.id))
    _audit(db, user, "update", "hazard", h.id, f"status {old}->{new.value}")
    db.commit()
    return {"ok": True, "status": new.value}


@router.post("/{hazard_id}/assign", dependencies=[VALIDATOR])
def assign(hazard_id: int, body: dict = Body(...), user=Depends(get_current_user),
           db: Session = Depends(get_db)):
    h = db.get(Hazard, hazard_id)
    if not h:
        raise HTTPException(404, "hazard not found")
    dept = body.get("department")
    if not dept:
        raise HTTPException(400, "department required")
    h.assigned_department = dept
    h.assigned_user_id = body.get("assigned_user_id")
    if h.status in (HazardStatus.OPEN, HazardStatus.PENDING_REVIEW, HazardStatus.REOPENED):
        old = h.status.value
        h.status = HazardStatus.IN_PROGRESS
        db.add(HazardStatusHistory(hazard_id=h.id, old_status=old, new_status="in_progress",
                                   note=f"assigned to {dept}", user_id=user.id))
    db.add(HazardAssignment(hazard_id=h.id, department=dept,
                            assigned_user_id=body.get("assigned_user_id"), assigned_by=user.id,
                            note=body.get("note")))
    _audit(db, user, "assign", "hazard", h.id, dept)
    db.commit()
    return {"ok": True, "department": dept}


@router.post("/{hazard_id}/merge", dependencies=[VALIDATOR])
def merge(hazard_id: int, body: dict = Body(...), user=Depends(get_current_user),
          db: Session = Depends(get_db)):
    """Fold this hazard into `into_id` (mark duplicate, move observations)."""
    into_id = body.get("into_id")
    h = db.get(Hazard, hazard_id)
    target = db.get(Hazard, into_id) if into_id else None
    if not h or not target:
        raise HTTPException(404, "hazard or target not found")
    for o in db.scalars(select(HazardObservation).where(HazardObservation.hazard_id == hazard_id)).all():
        o.hazard_id = target.id
        target.observation_count += 1
    h.status = HazardStatus.REJECTED
    h.duplicate_of = target.id
    db.add(HazardStatusHistory(hazard_id=h.id, old_status="", new_status="rejected",
                               note=f"merged into {target.id}", user_id=user.id))
    _audit(db, user, "update", "hazard", h.id, f"merge->{target.id}")
    db.commit()
    return {"ok": True, "into_id": target.id}


# ---------- AI scan trigger ----------
@router.post("/scan", dependencies=[VALIDATOR])
def scan_route(body: dict = Body(default={}), user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Queue a route's captured images for an OWL-ViT hazard scan (or all
    located images if no route given). The worker picks them up on-demand,
    loading the model only while there's work."""
    route_id = body.get("route_id")
    include_video = body.get("video", True)
    stmt = select(CapturedImage).where(CapturedImage.latitude.is_not(None))
    if route_id:
        stmt = stmt.where(CapturedImage.route_id == route_id)
    imgs = db.scalars(stmt).all()
    for im in imgs:
        im.hazard_pending, im.hazard_processed = True, False
    segs = []
    if include_video:
        sstmt = select(VideoSegment)
        if route_id:
            sstmt = sstmt.where(VideoSegment.route_id == route_id)
        segs = db.scalars(sstmt).all()
        for seg in segs:
            seg.hazard_pending, seg.hazard_processed = True, False
    _audit(db, user, "update", "hazard", None,
           f"queued {len(imgs)} images + {len(segs)} video segments for hazard scan")
    db.commit()
    return {"ok": True, "queued_images": len(imgs), "queued_segments": len(segs)}


# ---------- manual / resident / hotline intake ----------
@router.post("/intake", dependencies=[DRIVER])
def intake(body: dict = Body(...), user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Open a hazard from a non-AI source (staff spot / resident / hotline)."""
    try:
        src = HazardSource(body.get("source", "staff"))
    except ValueError:
        src = HazardSource.STAFF
    obs, hz = svc.ingest_observation(
        db, category_key=body["category_key"], confidence=body.get("confidence", 0.99),
        vehicle_lat=body.get("lat"), vehicle_lng=body.get("lng"), heading_deg=None,
        blocks_path=body.get("blocks_path", False), near_sensitive=body.get("near_sensitive", False),
        is_danger=body.get("is_danger", False), estimated_size=body.get("estimated_size"),
        source=src, detector_name=src.value, detector_version="manual",
    )
    if hz and hz.status == HazardStatus.PENDING_REVIEW and src != HazardSource.AI:
        hz.status = HazardStatus.OPEN  # a human-reported hazard is already confirmed
    _audit(db, user, "update", "hazard", hz.id if hz else None, f"intake {src.value}")
    db.commit()
    return {"ok": True, "hazard_id": hz.id if hz else None, "observation_id": obs.id}
