"""Realistic Buqata hazard sample data, so the full flow is demonstrable end to
end. Idempotent-ish: wipes prior sample hazards (source-tagged, notes marker)
and regenerates. Run: python -m app.seed_hazard_samples

Positions are real points inside Buqata (lat 33.19-33.21, lng 35.77-35.79).
Deterministic (no RNG) so re-runs are stable.
"""
from datetime import datetime, timedelta

from sqlalchemy import select, delete

from app.db.session import SessionLocal
from app.models import entities as _entities  # noqa: F401 — register users/routes for FK resolution
from app.models.hazards import (
    Hazard, HazardObservation, HazardStatusHistory, HazardStatus, HazardSeverity, HazardSource,
)

MARK = "sample"

# category, lat, lng, status, severity, obs, scan_days, dept, size, blocks, near_sensitive, danger, source, age_days
ROWS = [
    ("pothole",              33.20241, 35.77612, "open",         "high",     4, 2, "הנדסה",        "large",  True,  True,  False, "ai",       6),
    ("pothole",              33.20105, 35.77930, "in_progress",  "medium",   3, 2, "הנדסה",        "medium", False, False, False, "ai",       11),
    ("broken_manhole",       33.20320, 35.77480, "open",         "critical", 5, 3, "מים וביוב",    "medium", True,  True,  True,  "ai",       4),
    ("broken_manhole",       33.19980, 35.77860, "pending_review","high",    2, 1, "מים וביוב",    "small",  False, False, False, "ai",       1),
    ("garbage_pile",         33.20430, 35.77300, "open",         "medium",   3, 2, "תברואה",       "large",  False, False, False, "ai",       8),
    ("overflowing_bin",      33.20180, 35.77720, "in_progress",  "medium",   6, 3, "תברואה",       "medium", False, False, False, "ai",       9),
    ("construction_debris",  33.20260, 35.77990, "open",         "high",     4, 2, "פיקוח",        "large",  True,  False, False, "ai",       5),
    ("construction_materials",33.19960,35.77950, "pending_review","medium",  3, 3, "פיקוח",        "large",  False, False, False, "ai",       14),
    ("blocked_sidewalk",     33.20360, 35.77560, "open",         "high",     2, 1, "פיקוח",        "medium", True,  True,  False, "ai",       3),
    ("damaged_sign",         33.20090, 35.77650, "pending_review","medium",  2, 1, "תחבורה",       "small",  False, False, False, "ai",       2),
    ("stationary_vehicle",   33.20410, 35.77410, "pending_review","low",     4, 3, "פיקוח",        None,     False, False, False, "ai",       12),
    ("stationary_vehicle",   33.20030, 35.77800, "open",         "medium",   6, 4, "פיקוח",        None,     True,  False, False, "ai",       18),
    ("possible_business",    33.20150, 35.77690, "pending_review","low",     1, 1, "רישוי עסקים",  None,     False, False, False, "ai",       1),
    ("pothole",              33.20470, 35.77250, "likely_fixed", "medium",   3, 2, "הנדסה",        "small",  False, False, False, "ai",       20),
    ("garbage_pile",         33.20210, 35.77380, "closed",       "medium",   4, 3, "תברואה",       "medium", False, False, False, "resident", 25),
    ("broken_manhole",       33.20300, 35.77900, "open",         "high",     3, 2, "מים וביוב",    "medium", True,  False, True,  "hotline",  7),
]


def main():
    now = datetime.utcnow()
    with SessionLocal() as db:
        # clear prior samples
        ids = [h.id for h in db.scalars(select(Hazard).where(Hazard.notes == MARK)).all()]
        if ids:
            db.execute(delete(HazardObservation).where(HazardObservation.hazard_id.in_(ids)))
            db.execute(delete(HazardStatusHistory).where(HazardStatusHistory.hazard_id.in_(ids)))
            db.execute(delete(Hazard).where(Hazard.id.in_(ids)))
            db.commit()

        made = 0
        for (cat, lat, lng, status, sev, obs_n, scan_days, dept, size, blocks, near, danger, src, age) in ROWS:
            first = now - timedelta(days=age)
            hz = Hazard(
                category_key=cat, status=HazardStatus(status), severity=HazardSeverity(sev),
                confidence=0.6 + 0.3 * (sev in ("high", "critical")), latitude=lat, longitude=lng,
                location_accuracy_m=8.0, first_detected_at=first, last_detected_at=now - timedelta(days=max(0, age - scan_days)),
                observation_count=obs_n, distinct_scan_days=scan_days,
                assigned_department=dept, source=HazardSource(src), estimated_size=size,
                blocks_path=blocks, near_sensitive=near, is_danger=danger,
                missed_scans=2 if status == "likely_fixed" else 0,
                detector_version="owlvit", notes=MARK,
            )
            db.add(hz); db.flush()
            db.add(HazardStatusHistory(hazard_id=hz.id, old_status=None, new_status="detected",
                                       note="sample seed", created_at=first))
            if status != "detected":
                db.add(HazardStatusHistory(hazard_id=hz.id, old_status="detected", new_status=status,
                                           note="sample seed", created_at=now - timedelta(days=max(0, age - scan_days))))
            for i in range(obs_n):
                db.add(HazardObservation(
                    hazard_id=hz.id, category_key=cat, confidence=round(0.5 + 0.08 * i, 3),
                    confidence_band="high" if i == obs_n - 1 else "medium",
                    severity=HazardSeverity(sev), latitude=lat, longitude=lng,
                    location_accuracy_m=8.0, vehicle_latitude=lat, vehicle_longitude=lng,
                    image_quality="ok", detector_name="openvocab", detector_version="owlvit",
                    status="approved" if status not in ("pending_review",) else "pending",
                    captured_at=first + timedelta(days=i),
                ))
            made += 1
        db.commit()
        print(f"seeded {made} sample hazards with observations")


if __name__ == "__main__":
    main()
