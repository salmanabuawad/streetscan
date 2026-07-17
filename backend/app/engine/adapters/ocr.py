"""Local OCR adapter — Tesseract (heb+ara+eng), no cloud, no per-image fee.

Wraps app.ocr so the engine stays model-independent: swap this class for a
PaddleOCR adapter later without touching the pipeline.
"""
from __future__ import annotations

from app import ocr as _ocr


class LocalOCREngine:
    name = "tesseract"
    version = "heb+ara+eng"

    def read(self, image, box: list[float] | None = None) -> dict | None:
        crop = image
        if box is not None:
            x1, y1, x2, y2 = (int(v) for v in box)
            if x2 > x1 and y2 > y1:
                crop = image[y1:y2, x1:x2]
        res = _ocr.run_ocr_image(crop)
        if not res:
            return None
        return {
            "text": res["name"],
            "full_text": res["text"],
            "language": res["languages"],
            "confidence": res["confidence"],
            "suggested_category": res["category"],
        }
