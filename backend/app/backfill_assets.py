"""Create GIS assets for candidates that were approved before approval started
promoting them. Idempotent: skips candidates whose asset already exists.

Run:  python -m app.backfill_assets
"""
from sqlalchemy import select

from app.api.routes import promote_candidate_to_asset
from app.db.session import SessionLocal
from app.models.entities import CandidateAsset, CandidateStatus, Asset


def main():
    with SessionLocal() as db:
        approved = db.scalars(
            select(CandidateAsset).where(CandidateAsset.status == CandidateStatus.APPROVED)
            .order_by(CandidateAsset.id)
        ).all()
        existing = {
            a.notes.split(";")[0].replace("candidate ", "").strip()
            for a in db.scalars(select(Asset)).all() if a.notes and a.notes.startswith("candidate ")
        }
        made = 0
        for c in approved:
            if str(c.id) in existing:
                continue
            promote_candidate_to_asset(db, c)
            made += 1
        db.commit()
        total = db.scalar(select(Asset).with_only_columns(Asset.id).order_by(Asset.id.desc()).limit(1))
        print(f"backfilled {made} assets from {len(approved)} approved candidates")


if __name__ == "__main__":
    main()
