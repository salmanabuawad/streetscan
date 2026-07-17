"""Seed / upsert the asset_categories config table.

Categories, prompts and thresholds live in PostgreSQL (not hard-coded), so an
admin can add categories or retune the engine without a deploy.

Run:  python -m app.seed_categories
"""
from sqlalchemy import select

from app.db.session import Base, SessionLocal, engine
from app.models.entities import AssetCategory

# (name, layer, [prompts], detector, min_conf, department)
# detector "yolo" = covered by pretrained COCO (runs continuously on-server);
# "openvocab" = OWL-ViT text-prompted (batch / GPU). Config-driven — editable.
CATEGORIES = [
    # electricity
    ("electricity_pole", "electricity", ["electricity pole with wires"], "openvocab", 0.05, "electricity"),
    ("utility_pole", "electricity", ["utility pole"], "openvocab", 0.05, "electricity"),
    ("transformer", "electricity", ["electrical transformer"], "openvocab", 0.05, "electricity"),
    ("street_light", "electricity", ["street light lamp post"], "openvocab", 0.05, "electricity"),
    ("electrical_cabinet", "electricity", ["green electrical cabinet"], "openvocab", 0.05, "electricity"),
    ("overhead_electricity_cable", "electricity", ["power lines overhead cables"], "openvocab", 0.06, "electricity"),
    # telecom
    ("telecom_pole", "telecom", ["telecom pole"], "openvocab", 0.05, "telecom"),
    ("telephone_pole", "telecom", ["wooden telephone pole"], "openvocab", 0.05, "telecom"),
    ("telecom_cabinet", "telecom", ["metal telecom cabinet"], "openvocab", 0.05, "telecom"),
    ("junction_box", "telecom", ["telecom junction box"], "openvocab", 0.05, "telecom"),
    ("overhead_telecom_cable", "telecom", ["overhead telephone cable"], "openvocab", 0.06, "telecom"),
    # water
    ("hydrant", "water", ["fire hydrant"], "yolo", 0.30, "water"),
    ("water_meter", "water", ["water meter box"], "openvocab", 0.05, "water"),
    ("water_valve_cover", "water", ["water valve cover on ground"], "openvocab", 0.05, "water"),
    ("visible_water_pipe", "water", ["exposed water pipe"], "openvocab", 0.05, "water"),
    ("pump_equipment", "water", ["water pump equipment"], "openvocab", 0.05, "water"),
    # sewage & drainage
    ("manhole", "sewage", ["manhole cover on the road"], "openvocab", 0.05, "sewage"),
    ("manhole_cover", "sewage", ["round manhole cover"], "openvocab", 0.05, "sewage"),
    ("open_manhole", "sewage", ["open manhole hole in road"], "openvocab", 0.06, "sewage"),
    ("storm_drain", "drainage", ["storm drain grate"], "openvocab", 0.05, "drainage"),
    ("drain_inlet", "drainage", ["curb drain inlet"], "openvocab", 0.05, "drainage"),
    ("culvert", "drainage", ["road culvert"], "openvocab", 0.05, "drainage"),
    # roads
    ("traffic_sign", "road", ["traffic sign"], "yolo", 0.30, "roads"),
    ("street_name_sign", "road", ["street name sign"], "openvocab", 0.06, "roads"),
    ("road_marking", "road", ["painted road marking"], "openvocab", 0.06, "roads"),
    ("crosswalk", "road", ["pedestrian crosswalk"], "openvocab", 0.06, "roads"),
    ("speed_bump", "road", ["speed bump"], "openvocab", 0.06, "roads"),
    ("sidewalk", "road", ["sidewalk"], "openvocab", 0.08, "roads"),
    ("road_edge", "road", ["road edge curb"], "openvocab", 0.08, "roads"),
    ("pothole", "hazard", ["pothole in the road"], "openvocab", 0.06, "roads"),
    ("damaged_sidewalk", "hazard", ["damaged broken sidewalk"], "openvocab", 0.06, "roads"),
    # public space
    ("garbage_container", "public_space", ["garbage container dumpster"], "openvocab", 0.05, "sanitation"),
    ("public_bin", "public_space", ["public trash bin"], "openvocab", 0.05, "sanitation"),
    ("bench", "public_space", ["bench"], "yolo", 0.30, "public_space"),
    ("bus_stop", "public_space", ["bus stop shelter"], "openvocab", 0.05, "public_space"),
    ("tree", "public_space", ["tree"], "yolo", 0.30, "parks"),
    ("fence", "public_space", ["metal fence"], "openvocab", 0.06, "public_space"),
    ("retaining_wall", "public_space", ["stone retaining wall"], "openvocab", 0.06, "public_space"),
    ("playground_equipment", "public_space", ["playground equipment"], "openvocab", 0.05, "parks"),
    # buildings & signs
    ("commercial_sign", "building", ["commercial storefront sign"], "openvocab", 0.05, "licensing"),
    ("institution_sign", "building", ["public institution sign"], "openvocab", 0.05, "licensing"),
    ("public_building", "building", ["public building"], "openvocab", 0.06, "engineering"),
    ("municipal_building", "building", ["municipal building"], "openvocab", 0.06, "municipal"),
    ("school", "building", ["school building"], "openvocab", 0.06, "education"),
    ("clinic", "building", ["medical clinic"], "openvocab", 0.06, "health"),
    ("supermarket", "building", ["supermarket"], "openvocab", 0.06, "licensing"),
    ("barber_shop", "building", ["barber shop"], "openvocab", 0.06, "licensing"),
    ("restaurant", "building", ["restaurant"], "openvocab", 0.06, "licensing"),
    ("hotel", "building", ["hotel"], "openvocab", 0.06, "tourism"),
    # hazards
    ("illegal_dumping", "hazard", ["pile of garbage on the street"], "openvocab", 0.06, "sanitation"),
    ("overflowing_garbage", "hazard", ["overflowing garbage bin"], "openvocab", 0.06, "sanitation"),
    ("damaged_cabinet", "hazard", ["damaged electrical cabinet"], "openvocab", 0.06, "electricity"),
    ("broken_sign", "hazard", ["broken bent traffic sign"], "openvocab", 0.06, "roads"),
    ("leaning_pole", "hazard", ["leaning tilted utility pole"], "openvocab", 0.06, "electricity"),
    ("exposed_cable", "hazard", ["dangling exposed electrical cable"], "openvocab", 0.06, "electricity"),
    ("vegetation_blocking", "hazard", ["vegetation covering a sign"], "openvocab", 0.06, "parks"),
    ("missing_manhole_cover", "hazard", ["missing manhole cover open hole"], "openvocab", 0.06, "sewage"),
    ("road_obstacle", "hazard", ["obstacle blocking the road"], "openvocab", 0.06, "roads"),
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
