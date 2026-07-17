"""StreetScan asset-analysis engine — model-independent, replaceable adapters."""
from app.engine.base import (
    AssetAnalysisEngine, AssetAnalysisResult, CaptureContext, CategoryPrompt, DetectedAsset,
)
from app.engine.pipeline import DefaultAssetAnalysisEngine

__all__ = [
    "AssetAnalysisEngine", "AssetAnalysisResult", "CaptureContext", "CategoryPrompt",
    "DetectedAsset", "DefaultAssetAnalysisEngine",
]
