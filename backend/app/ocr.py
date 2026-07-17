"""Storefront/business recognition from sign images via Tesseract OCR.

Tesseract (heb+ara+eng) is used instead of EasyOCR because it supports all
three languages Buqata signs use, runs in ~64 MB (no torch), and needs no
resident model — so it folds into the existing YOLO worker with no extra
service on the shared 4 GB box.
"""
import re

import cv2
import pytesseract

# Keyword -> canonical category. Arabic / Hebrew / English variants each map
# to the same category so a sign in any language is classified consistently.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "pharmacy":    ["صيدلية", "בית מרקחת", "מרקחת", "pharmacy", "pharmacie"],
    "clinic":      ["عيادة", "מרפאה", "clinic", "medical"],
    "dentist":     ["اسنان", "أسنان", "שיניים", "dental", "dentist"],
    "supermarket": ["سوبر ماركت", "سوبرماركت", "بقالة", "סופרמרקט", "סופר", "מרכול", "supermarket", "market"],
    "grocery":     ["بقالة", "مواد غذائية", "מכולת", "grocery"],
    "restaurant":  ["مطعم", "مطاعم", "מסעדה", "restaurant", "grill", "شاورما"],
    "cafe":        ["مقهى", "كافيه", "קפה", "cafe", "coffee", "قهوة"],
    "bakery":      ["مخبز", "فرن", "מאפייה", "bakery", "معجنات"],
    "barber":      ["حلاق", "صالون حلاقة", "מספרה", "barber", "salon حلاقة"],
    "beauty":      ["تجميل", "صالون تجميل", "יופי", "קוסמטיקה", "beauty", "cosmetics"],
    "bank":        ["بنك", "مصرف", "בנק", "bank"],
    "garage":      ["كراج", "ورشة", "מוסך", "garage", "mechanic", "ميكانيك"],
    "clothing":    ["ملابس", "أزياء", "בגדים", "אופנה", "clothing", "fashion", "boutique"],
    "hardware":    ["أدوات", "حديد", "כלי עבודה", "hardware", "بناء"],
    "mosque":      ["مسجد", "جامع", "مصلى", "mosque"],
    "school":      ["مدرسة", "בית ספר", "school", "روضة", "גן ילדים", "kindergarten"],
    "municipal":   ["بلدية", "مجلس", "עירייה", "מועצה", "municipal", "council"],
    "sports":      ["نادي", "رياضة", "מועדון", "כושר", "gym", "fitness", "sport"],
    "hotel":       ["فندق", "نزل", "מלון", "hotel", "guest"],
}

# Text this short or this un-word-like is sign noise, not a business name.
MIN_TOKEN_LEN = 2
MIN_LINE_CONF = 45.0


def detect_languages(text: str) -> str:
    langs = []
    if re.search(r"[؀-ۿ]", text):
        langs.append("ar")
    if re.search(r"[֐-׿]", text):
        langs.append("he")
    if re.search(r"[A-Za-z]", text):
        langs.append("en")
    return ",".join(langs)


def suggest_category(text: str) -> str:
    low = text.lower()
    for category, words in CATEGORY_KEYWORDS.items():
        if any(w.lower() in low for w in words):
            return category
    return "unknown"


def clean_line(line: str) -> str:
    # keep letters (latin/arabic/hebrew), digits and spaces; drop OCR symbol noise
    cleaned = re.sub(r"[^\w֐-׿؀-ۿ \-&']", " ", line)
    return re.sub(r"\s+", " ", cleaned).strip()


def run_ocr(image_path: str) -> dict | None:
    """OCR a sign image. Returns {name, category, text, languages, confidence}
    or None when no confident business-like text is found."""
    img = cv2.imread(image_path)
    if img is None:
        return None
    # Upscale small images a little; Tesseract likes taller text.
    h, w = img.shape[:2]
    if max(h, w) < 1400:
        scale = 1400 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    data = pytesseract.image_to_data(
        gray, lang="heb+ara+eng", output_type=pytesseract.Output.DICT
    )

    # Group words into lines, keeping each line's mean confidence and the
    # height of its text (bigger text = more likely an actual shop sign).
    lines: dict[tuple, dict] = {}
    for i, word in enumerate(data["text"]):
        conf = float(data["conf"][i])
        token = clean_line(word)
        if conf < MIN_LINE_CONF or len(token) < MIN_TOKEN_LEN:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        entry = lines.setdefault(key, {"words": [], "confs": [], "height": 0})
        entry["words"].append(token)
        entry["confs"].append(conf)
        entry["height"] = max(entry["height"], int(data["height"][i]))

    if not lines:
        return None

    # Best line = largest text with decent confidence (the shop's main sign).
    best = max(lines.values(), key=lambda e: e["height"] * (sum(e["confs"]) / len(e["confs"])))
    name = " ".join(best["words"]).strip()
    if len(name) < 2:
        return None
    all_text = " | ".join(" ".join(e["words"]) for e in lines.values())
    mean_conf = sum(best["confs"]) / len(best["confs"]) / 100.0

    return {
        "name": name[:200],
        "category": suggest_category(all_text),
        "text": all_text[:2000],
        "languages": detect_languages(all_text),
        "confidence": round(mean_conf, 3),
    }
