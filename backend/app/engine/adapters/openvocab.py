"""Local open-vocabulary detector adapter using OWL-ViT.

The adapter is deliberately model-independent from the rest of StreetScan. It
runs completely on the municipal server, supports CUDA when available, and
chunks prompts to keep CPU/RAM usage predictable.
"""
from __future__ import annotations

import os
from typing import Iterable

from app.engine.base import CategoryPrompt, DetectedAsset


class OpenVocabDetector:
    name = "owlvit"

    def __init__(
        self,
        model_id: str = "google/owlvit-base-patch32",
        threshold: float = 0.04,
        prompt_batch_size: int = 12,
    ):
        self.model_id = model_id
        self.version = model_id.rsplit("/", 1)[-1]
        self.threshold = threshold
        self.prompt_batch_size = max(1, prompt_batch_size)
        self._proc = None
        self._model = None
        self._device = "cpu"

    def _ensure_loaded(self):
        if self._model is not None:
            return
        import torch
        from transformers import OwlViTForObjectDetection, OwlViTProcessor

        torch.set_num_threads(max(1, int(os.getenv("STREETSCAN_TORCH_THREADS", "4"))))
        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._proc = OwlViTProcessor.from_pretrained(self.model_id)
        self._model = OwlViTForObjectDetection.from_pretrained(self.model_id).eval().to(self._device)

    @staticmethod
    def _chunks(items: list, size: int) -> Iterable[list]:
        for i in range(0, len(items), size):
            yield items[i:i + size]

    def detect(self, image, prompts: list[CategoryPrompt]) -> list[DetectedAsset]:
        self._ensure_loaded()
        from PIL import Image
        import cv2

        prompt_rows: list[tuple[str, CategoryPrompt]] = []
        for category in prompts:
            for raw in category.prompts:
                prompt = raw.strip()
                if prompt:
                    prompt_rows.append((prompt, category))
        if not prompt_rows:
            return []

        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        detections: list[DetectedAsset] = []

        # OWL-ViT can accept many text queries, but smaller chunks are much more
        # stable on CPU-only municipal servers and avoid large memory spikes.
        for batch in self._chunks(prompt_rows, self.prompt_batch_size):
            text = [row[0] for row in batch]
            inputs = self._proc(text=[text], images=pil, return_tensors="pt")
            inputs = {k: v.to(self._device) if hasattr(v, "to") else v for k, v in inputs.items()}
            with self._torch.inference_mode():
                outputs = self._model(**inputs)

            target_sizes = self._torch.tensor([pil.size[::-1]], device=self._device)
            # transformers >=5 renamed this to post_process_grounded_object_detection
            # and dropped post_process_object_detection for OWL-ViT; support both.
            post = getattr(self._proc, "post_process_grounded_object_detection", None) \
                or self._proc.post_process_object_detection
            result = post(outputs=outputs, threshold=self.threshold, target_sizes=target_sizes)[0]

            # labels are integer query indices; some versions return the matched
            # prompt strings under "text_labels" instead — handle both.
            labels = result.get("labels", result.get("text_labels"))
            batch_prompts = [row[0] for row in batch]
            for score, label, box in zip(result["scores"], labels, result["boxes"]):
                if isinstance(label, str):
                    if label not in batch_prompts:
                        continue
                    index = batch_prompts.index(label)
                else:
                    index = int(label.detach().cpu().item()) if hasattr(label, "detach") else int(label)
                if index < 0 or index >= len(batch):
                    continue
                prompt, category = batch[index]
                confidence = float(score.detach().cpu().item())
                if confidence < category.min_confidence:
                    continue
                detections.append(DetectedAsset(
                    proposed_category=category.name,
                    infrastructure_layer=category.infrastructure_layer,
                    confidence=round(confidence, 4),
                    box=[float(x) for x in box.detach().cpu().tolist()],
                    detector_name=self.name,
                    detector_version=self.version,
                    warnings=[f"prompt:{prompt}", f"device:{self._device}"],
                ))
        return detections
