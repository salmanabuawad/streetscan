"""Asset Detection Engine — domain sub-engines.

The Asset Engine is not one monolith: it is a set of domain sub-engines, each
scoped to an infrastructure layer with its own categories, prompts and (in
future) its own model. A sub-engine is just the Asset Engine composed over the
AssetCategory rows whose `infrastructure_layer` matches the domain — so adding a
domain is configuration, not code.

This mirrors the Hazard Detection Engine, which is the same composition over
HazardCategory rows plus hazard-specific analyzers (tilt, image-quality).

See docs/ENGINES.md for the full architecture and the pluggable-engine contract.
"""
from __future__ import annotations

# domain key -> (Hebrew label, infrastructure_layer values it covers)
ASSET_SUBENGINES: dict[str, tuple[str, tuple[str, ...]]] = {
    "electricity":   ("חשמל", ("electricity",)),
    "communication": ("תקשורת", ("telecom",)),
    "water":         ("מים", ("water",)),
    "sewage":        ("ביוב וניקוז", ("sewage", "drainage")),
    "road":          ("כבישים ותמרור", ("road",)),
    "public_space":  ("מרחב ציבורי", ("public_space",)),
}


def layers_for(domain: str) -> tuple[str, ...]:
    entry = ASSET_SUBENGINES.get(domain)
    return entry[1] if entry else ()


def build_asset_subengine(db, domain: str, detector):
    """Compose the Asset Engine scoped to one domain (e.g. 'electricity').
    Shares a detector instance with the full engine — one model, scoped prompts.
    Returns None if the domain has no active categories."""
    from pathlib import Path
    from sqlalchemy import select
    from app.core.config import settings
    from app.models.entities import AssetCategory
    from app.engine import DefaultAssetAnalysisEngine, CategoryPrompt

    layers = layers_for(domain)
    if not layers:
        return None
    cats = db.scalars(select(AssetCategory).where(
        AssetCategory.active, AssetCategory.infrastructure_layer.in_(layers))).all()
    if not cats:
        return None
    prompts = [CategoryPrompt(
        name=c.name, infrastructure_layer=c.infrastructure_layer,
        prompts=c.detection_prompts.split("\n"), min_confidence=c.min_confidence,
        active_detector=c.active_detector, requires_validation=c.requires_validation,
    ) for c in cats]
    return DefaultAssetAnalysisEngine(
        detector=detector, categories=prompts,
        crops_dir=str(Path(settings.upload_dir) / "crops"),
        annotated_dir=str(Path(settings.upload_dir) / "annotated"),
    )
