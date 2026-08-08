"""Short-lived, single-worker image feature extraction."""

from typing import TYPE_CHECKING

from .exceptions import (
    ExtractionCancelledError,
    ExtractionTimeoutError,
    ImageFeaturesExtractorError,
    WorkerCrashedError,
    WorkerInitializationError,
)
from .extractor import extract_features, iter_features, normalize_image_path
from .models import (
    FEATURE_VECTOR_SIZE,
    ExtractionProgress,
    ExtractionRequest,
    ExtractionSummary,
    FeatureResultBatch,
    ImageFeatureResult,
    WorkerStartupInfo,
)

if TYPE_CHECKING:
    from .qt import QtImageFeaturesExtractor

__all__ = [
    "FEATURE_VECTOR_SIZE",
    "ExtractionCancelledError",
    "ExtractionProgress",
    "ExtractionRequest",
    "ExtractionSummary",
    "ExtractionTimeoutError",
    "FeatureResultBatch",
    "ImageFeatureResult",
    "ImageFeaturesExtractorError",
    "QtImageFeaturesExtractor",
    "WorkerCrashedError",
    "WorkerInitializationError",
    "WorkerStartupInfo",
    "extract_features",
    "iter_features",
    "normalize_image_path",
]


def __getattr__(name: str):
    if name == "QtImageFeaturesExtractor":
        from .qt import QtImageFeaturesExtractor

        return QtImageFeaturesExtractor
    raise AttributeError(name)
