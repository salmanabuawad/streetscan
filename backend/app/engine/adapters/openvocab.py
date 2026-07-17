"""Open-vocabulary detector adapter — OWL-ViT (text-prompted, local).

Detects the municipal categories that no fixed-class model (COCO YOLO) knows —
utility poles, cabinets, manholes, etc. — from the config-table prompts.

transformers/torch are imported lazily so a CPU-only or model-disabled server
does not need them installed until this adapter is actually activated.
"""
from __future__ import annotations

import time

from app.engine.base import CategoryPrompt, DetectedAsset


class OpenVocabDetector:
    name = "owlvit"
    version = "base-patch32"

    def __init__(self, model_id: str = "google/owlvit-base-patch32", threshold: float = 0.04):
        self.model_id = model_id
        self.threshold = threshold
        self._proc = None
        self._model = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        import torch
        from transformers import OwlViTProcessor, OwlViTForObjectDetection
        torch.set_num_threads(4)
        self._torch = torch
        self._proc = OwlViTProcessor.from_pretrained(self.model_id)
        self._model = OwlViTForObjectDetection.from_pretrained(self.model_id).eval()

    def detect(self, image, prompts: list[CategoryPrompt]) -> list[DetectedAsset]:
        self._ensure_loaded()
        import numpy as np
        from PIL import Image
        import cv2

        # flatten prompts -> category lookup
        text: list[str] = []
        owner: dict[str, CategoryPrompt] = {}
        for c in prompts:
            for p in c.prompts:
                text.append(p)
                owner[p] = c

        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        inp = self._proc(text=[text], images=pil, return_tensors="pt")
        t0 = time.time()
        with self._torch.no_grad():
            out = self._model(**inp)
        target = self._torch.tensor([pil.size[::-1]])
        post = getattr(self._proc, "post_process_grounded_object_detection", None) \
            or self._proc.post_process_object_detection
        res = post(out, threshold=self.threshold, target_sizes=target)[0]
        labels = res.get("labels", res.get("text_labels"))

        dets: list[DetectedAsset] = []
        for score, label, box in zip(res["scores"], labels, res["boxes"]):
            prompt = label if isinstance(label, str) else text[int(label)]
            cat = owner[prompt]
            s = float(score)
            if s < cat.min_confidence:
                continue
            dets.append(DetectedAsset(
                proposed_category=cat.name,
                infrastructure_layer=cat.infrastructure_layer,
                confidence=round(s, 3),
                box=[float(x) for x in box.tolist()],
                detector_name=self.name,
                detector_version=self.version,
            ))
        return dets
