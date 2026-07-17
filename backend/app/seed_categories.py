"""Seed / upsert the asset_categories config table.

Categories, prompts and thresholds live in PostgreSQL (not hard-coded), so an
admin can add categories or retune the engine without a deploy.

Run:  python -m app.seed_categories
"""
from sqlalchemy import select

from app.db.session import Base, SessionLocal, engine
from app.models.entities import AssetCategory

# (name, layer, [prompts], detector, min_conf, department)
CATEGORIES = [
    ("utility_pole", "electricity", ["utility pole"], "openvocab", 0.05, "electricity"),
    ("electricity_pole", "electricity", ["electricity pole with wires"], "openvocab", 0.05, "electricity"),
    ("transformer", "electricity", ["electrical transformer"], "openvocab", 0.05, "electricity"),
    ("street_light", "electricity", ["street light lamp post"], "openvocab", 0.05, "electricity"),
    ("electrical_cabinet", "electricity", ["green electrical cabinet"], "openvocab", 0.05, "electricity"),
    ("overhead_cable", "electricity", ["power lines overhead cables"], "openvocab", 0.06, "electricity"),
    ("telecom_pole", "telecom", ["wooden telephone pole"], "openvocab", 0.05, "telecom"),
    ("telecom_cabinet", "telecom", ["metal telecom cabinet"], "openvocab", 0.05, "telecom"),
    ("junction_box", "telecom", ["telecom junction box"], "openvocab", 0.05, "telecom"),
    ("hydrant", "water", ["fire hydrant"], "openvocab", 0.05, "water"),
    ("water_valve_cover", "water", ["water valve cover on ground"], "openvocab", 0.05, "water"),
    ("manhole", "sewage", ["manhole cover on the road"], "openvocab", 0.05, "sewage"),
    ("storm_drain", "drainage", ["storm drain grate"], "openvocab", 0.05, "drainage"),
    ("traffic_sign", "road", ["traffic sign"], "openvocab", 0.06, "roads"),
    ("street_name_sign", "road", ["street name sign"], "openvocab", 0.06, "roads"),
    ("pothole", "hazard", ["pothole in the road"], "openvocab", 0.06, "roads"),
    ("damaged_sidewalk", "hazard", ["damaged broken sidewalk"], "openvocab", 0.06, "roads"),
    ("garbage_container", "public_space", ["garbage container dumpster"], "openvocab", 0.05, "sanitation"),
    ("bench", "public_space", ["bench"], "openvocab", 0.05, "public_space"),
    ("bus_stop", "public_space", ["bus stop shelter"], "openvocab", 0.05, "public_space"),
    ("tree", "public_space", ["tree"], "openvocab", 0.07, "parks"),
    ("retaining_wall", "public_space", ["stone retaining wall"], "openvocab", 0.06, "public_space"),
    ("fence", "public_space", ["metal fence"], "openvocab", 0.06, "public_space"),
    ("commercial_sign", "building", ["commercial storefront sign"], "openvocab", 0.05, "licensing"),
    ("illegal_dumping", "hazard", ["pile of garbage on the street"], "openvocab", 0.06, "sanitation"),
]


def main():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        existing = {c.name for c in db.scalars(select(AssetCategory))}
        added = 0
        for name, layer, prompts, det, conf, dept in CATEGORIES:
            if name in existing:
                continue
            db.add(AssetCategory(
                name=name, infrastructure_layer=layer, detection_prompts="\n".join(prompts),
                active_detector=det, min_confidence=conf, department=dept, active=True,
            ))
            added += 1
        db.commit()
        print(f"asset_categories: {added} added, {len(existing)} already present")


if __name__ == "__main__":
    main()
