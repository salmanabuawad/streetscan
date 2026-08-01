"""Seed the MVP hazard categories + severity rules for Buqata. Idempotent
(upsert by key). Run: python -m app.seed_hazards

NOTE on thresholds: min_confidence is on the ACTIVE DETECTOR's scale. OWL-ViT
open-vocab logits sit near 0.05-0.20, so MVP thresholds are set on that scale
(not the 0.75-style numbers in the product spec, which assume a trained model).
When a municipal hazard model is trained, raise these via the admin UI.
"""
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.hazards import HazardCategory, HazardSeverityRule, HazardSeverity

# key, he, ar, en, group, detector, prompts, min_conf, auto_open, severity,
# dedup_m, department, icon, color
CATS = [
    ("pothole", "בור בכביש", "حفرة في الطريق", "Pothole", "road", "openvocab",
     ["a pothole in the road", "a hole in the asphalt", "road surface damage", "a deep hole in the street"],
     0.11, True, HazardSeverity.MEDIUM, 3.0, "הנדסה", "alert-triangle", "#f97316"),
    ("broken_manhole", "מכסה ביוב פגום", "غطاء مجاري مكسور", "Broken manhole", "road", "openvocab",
     ["a broken manhole cover", "a missing manhole cover", "an open sewer drain in the road", "a sunken manhole"],
     0.10, True, HazardSeverity.HIGH, 3.0, "מים וביוב", "circle-dot", "#dc2626"),
    ("construction_debris", "פסולת בנייה", "مخلفات بناء", "Construction debris", "construction", "openvocab",
     ["a pile of construction debris", "rubble on the road", "broken concrete pieces", "demolition waste on the street"],
     0.60, False, HazardSeverity.MEDIUM, 6.0, "פיקוח", "bricks", "#a16207"),
    ("garbage_pile", "ערימת אשפה", "كومة نفايات", "Garbage pile", "sanitation", "openvocab",
     ["a pile of garbage bags", "trash on the ground", "a heap of household waste", "illegal dumping of garbage"],
     0.12, True, HazardSeverity.MEDIUM, 6.0, "תברואה", "trash", "#16a34a"),
    ("blocked_sidewalk", "מדרכה חסומה", "رصيف مسدود", "Blocked sidewalk", "sidewalk", "openvocab",
     ["objects blocking a sidewalk", "a sidewalk blocked by materials", "an obstacle on the pavement"],
     0.12, True, HazardSeverity.MEDIUM, 5.0, "פיקוח", "footprints", "#7c3aed"),
    ("overflowing_bin", "פח מלא או שבור", "حاوية ممتلئة أو مكسورة", "Overflowing/broken bin", "sanitation", "openvocab",
     ["an overflowing trash bin", "a full garbage container", "a broken dumpster", "an overturned waste bin"],
     0.11, True, HazardSeverity.MEDIUM, 4.0, "תברואה", "trash-2", "#0891b2"),
    ("damaged_sign", "תמרור פגום", "لافتة تالفة", "Damaged sign", "signage", "openvocab",
     ["a fallen traffic sign", "a bent traffic sign", "a leaning road sign", "a damaged street sign"],
     0.11, True, HazardSeverity.MEDIUM, 4.0, "תחבורה", "sign-post", "#2563eb"),
    ("stationary_vehicle", "רכב שנצפה לאורך זמן", "مركبة متوقفة لفترة طويلة", "Long-stationary vehicle", "vehicle", "yolo",
     ["a parked car", "an abandoned vehicle", "a car parked on the street"],
     0.45, False, HazardSeverity.LOW, 8.0, "פיקוח", "car", "#64748b"),
    ("construction_materials", "חומרי בנייה במרחב הציבורי", "مواد بناء في المجال العام", "Construction materials in public space", "construction", "openvocab",
     ["a pile of sand or gravel on the street", "building blocks stacked on the road", "a construction sand pile", "bags of cement on the sidewalk"],
     0.60, False, HazardSeverity.MEDIUM, 8.0, "פיקוח", "package", "#b45309"),
    ("possible_business", "עסק אפשרי לפי שילוט", "عمل تجاري محتمل حسب اللافتة", "Possible business (by signage)", "business", "ocr",
     ["a shop storefront sign", "a business sign", "a store front"],
     0.35, False, HazardSeverity.LOW, 10.0, "רישוי עסקים", "store", "#db2777"),
    # leaning poles/trees: detected as poles by the Asset Engine, tilt-judged by
    # the Hazard Engine's tilt analyzer. Never auto-opens — always field-inspected.
    ("leaning_pole", "עמוד או עץ נוטה", "عمود أو شجرة مائلة", "Leaning pole/tree", "infrastructure", "tilt",
     ["a leaning utility pole", "a tilted electricity pole", "a leaning street light pole", "a tree leaning over the road"],
     0.0, False, HazardSeverity.MEDIUM, 4.0, "חשמל ותחזוקה", "utility-pole", "#e11d48"),
]


def main():
    with SessionLocal() as db:
        for (key, he, ar, en, grp, det, prompts, conf, auto, sev, dedup, dept, icon, color) in CATS:
            row = db.scalar(select(HazardCategory).where(HazardCategory.key == key))
            if row is None:
                row = HazardCategory(key=key)
                db.add(row)
            row.name_he, row.name_ar, row.name_en = he, ar, en
            row.group, row.active_detector = grp, det
            row.detection_prompts = "\n".join(prompts)
            row.min_confidence, row.auto_open_allowed = conf, auto
            row.default_severity, row.dedup_radius_m = sev, dedup
            row.department, row.icon, row.color = dept, icon, color
            row.is_mvp, row.active = True, True
        # one global severity rule (admin can add per-category overrides later)
        if not db.scalar(select(HazardSeverityRule).where(HazardSeverityRule.category_key.is_(None))):
            db.add(HazardSeverityRule(category_key=None, base_severity=HazardSeverity.MEDIUM))
        db.commit()
        n = db.scalar(select(HazardCategory).with_only_columns(HazardCategory.id))
        print(f"seeded {len(CATS)} hazard categories")


if __name__ == "__main__":
    main()
