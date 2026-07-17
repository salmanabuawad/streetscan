"""Model-independent asset-analysis engine contract.

The core architectural principle: StreetScan never embeds one AI model. It
talks to an `AssetAnalysisEngine` composed of replaceable adapters (object
detection, open-vocabulary detection, OCR, segmentation, embeddings, condition).
Models are swapped via configuration + the model registry without touching the
API, database, GIS workflow or validation UI.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CaptureContext:
    """Everything the engine knows about where a frame came from."""
    image_id: int | None = None
    route_id: int | None = None
    image_sequence: int | None = None
    capture_type: str | None = None            # interval | stop_burst | manual | video_frame
    latitude: float | None = None
    longitude: float | None = None
    heading_deg: float | None = None
    quality_score: float | None = None
    source: str = "image"                      # image | video_frame | zip


@dataclass
class DetectedAsset:
    proposed_category: str
    infrastructure_layer: str
    confidence: float
    box: list[float]                            # [x1, y1, x2, y2] pixels
    detector_name: str
    detector_version: str
    mask: list | None = None                    # segmentation, when available
    ocr_text: str | None = None
    ocr_language: str | None = None
    ocr_confidence: float | None = None
    condition: str | None = None                # good | worn | damaged | unknown
    defect: str | None = None
    crop_path: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class AssetAnalysisResult:
    context: CaptureContext
    assets: list[DetectedAsset] = field(default_factory=list)
    annotated_path: str | None = None
    model_name: str = ""
    model_version: str = ""
    processing_ms: int = 0
    warnings: list[str] = field(default_factory=list)


# ---- replaceable adapter contracts ---------------------------------------

class ObjectDetector(Protocol):
    name: str
    version: str
    def detect(self, image, prompts: list["CategoryPrompt"]) -> list[DetectedAsset]: ...


class OCREngine(Protocol):
    name: str
    version: str
    def read(self, image, box: list[float] | None = None) -> dict | None: ...


class SegmentationEngine(Protocol):
    name: str
    version: str
    def mask(self, image, box: list[float]) -> list | None: ...


class EmbeddingEngine(Protocol):
    name: str
    version: str
    def embed(self, image) -> list[float]: ...


class ConditionEngine(Protocol):
    def assess(self, image, asset: DetectedAsset) -> tuple[str | None, str | None]: ...


@dataclass
class CategoryPrompt:
    """One row from the PostgreSQL asset_categories config table."""
    name: str
    infrastructure_layer: str
    prompts: list[str]
    min_confidence: float
    active_detector: str                        # yolo | openvocab
    requires_validation: bool = True


class AssetAnalysisEngine(ABC):
    """The single seam the rest of StreetScan depends on."""

    @abstractmethod
    def analyze_image(self, image_path: str, context: CaptureContext) -> AssetAnalysisResult:
        ...
