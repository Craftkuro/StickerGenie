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
from .model_specs import (
    DEFAULT_MODEL_FILENAME,
    DEFAULT_MODEL_SPEC,
    ImageFeatureModelSpec,
    get_model_spec,
)

if TYPE_CHECKING:
    from .qt import QtImageFeaturesExtractor

__all__ = [
    "DEFAULT_MODEL_FILENAME",
    "DEFAULT_MODEL_SPEC",
    "FEATURE_VECTOR_SIZE",
    "ExtractionCancelledError",
    "ExtractionProgress",
    "ExtractionRequest",
    "ExtractionSummary",
    "ExtractionTimeoutError",
    "FeatureResultBatch",
    "ImageFeatureResult",
    "ImageFeatureModelSpec",
    "ImageFeaturesExtractorError",
    "QtImageFeaturesExtractor",
    "WorkerCrashedError",
    "WorkerInitializationError",
    "WorkerStartupInfo",
    "extract_features",
    "get_model_spec",
    "iter_features",
    "normalize_image_path",
]


def __getattr__(name: str):
    if name == "QtImageFeaturesExtractor":
        from .qt import QtImageFeaturesExtractor

        return QtImageFeaturesExtractor
    raise AttributeError(name)
