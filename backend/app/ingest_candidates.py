"""Ingest engine detection results (candidates.csv) into production tables.

Turns the raw per-frame detections into CandidateAsset rows and groups repeated
observations of the same asset (same route + category, consecutive frames) into
ProposedAsset rows — so we don't create a new asset for every frame.

The image_sequence in the export IS the captured_images.id (filenames are
route<R>_img<ID>_...), so candidates link straight to the source frame and
inherit its GPS. No coordinates are invented.

Run:  python -m app.ingest_candidates <candidates.csv>
"""
import csv
import sys

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import CandidateAsset, ProposedAsset, CapturedImage, CandidateStatus

HIGH, MED = 0.10, 0.06
SEQ_GAP = 4   # frames within this gap + same category = same physical asset


def band(score: float) -> str:
    return "high" if score >= HIGH else "medium" if score >= MED else "low"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "candidates.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    with SessionLocal() as db:
        # image_id -> (route_id, lat, lng)
        imgs = {i.id: i for i in db.scalars(select(CapturedImage))}
        created = 0
        for r in rows:
            img_id = int(r["image_sequence"])       # == captured_images.id
            img = imgs.get(img_id)
            score = float(r["score"])
            db.add(CandidateAsset(
                image_id=img_id if img else None,
                route_id=(img.route_id if img else int(r["route_id"] or 0) or None),
                image_sequence=img_id,
                capture_type=r.get("capture_type"),
                proposed_category=r["category"],
                infrastructure_layer=r["layer"],
                confidence=score,
                bbox=r["box"].strip("[]").replace(" ", ""),
                quality_score=float(r["quality_score"]) if r.get("quality_score") else None,
                dup_group=r.get("dup_group"),
                detector_name=r.get("model", "owlvit"),
                detector_version="base-patch32",
                latitude=(img.latitude if img else None),
                longitude=(img.longitude if img else None),
                confidence_band=band(score),
                status=CandidateStatus.PENDING_VALIDATION,
            ))
            created += 1
        db.commit()

        # group into proposed assets: same route + category, frames close together
        cands = db.scalars(select(CandidateAsset).order_by(
            CandidateAsset.route_id, CandidateAsset.proposed_category, CandidateAsset.image_sequence
        )).all()
        groups = 0
        prev = None
        current: ProposedAsset | None = None
        for c in cands:
            key = (c.route_id, c.proposed_category)
            gap = abs((c.image_sequence or 0) - (prev.image_sequence or 0)) if prev else 999
            same = (prev and (prev.route_id, prev.proposed_category) == key and gap <= SEQ_GAP)
            if not same:
                current = ProposedAsset(
                    category=c.proposed_category, infrastructure_layer=c.infrastructure_layer,
                    route_id=c.route_id, observation_count=0, best_confidence=0.0,
                    latitude=c.latitude, longitude=c.longitude,
                    status=CandidateStatus.PENDING_VALIDATION,
                )
                db.add(current)
                db.flush()
                groups += 1
            current.observation_count += 1
            if c.confidence > current.best_confidence:
                current.best_confidence = c.confidence
                current.best_candidate_id = c.id
                if c.latitude is not None:
                    current.latitude, current.longitude = c.latitude, c.longitude
            c.proposed_asset_id = current.id
            prev = c
        db.commit()
        print(f"ingested {created} candidate detections into {groups} proposed assets")


if __name__ == "__main__":
    main()
