"""Short-lived batch feature extraction jobs built on :mod:`batch_job_runner`."""

from .exceptions import (
    ExtractionCancelledError,
    ExtractionTimeoutError,
    ImageFeaturesExtractorError,
    WorkerCrashedError,
    WorkerInitializationError,
)
from .model_specs import (
    DEFAULT_MODEL_FILENAME,
    DEFAULT_MODEL_SPEC,
    ImageFeatureModelSpec,
    get_model_spec,
)
from .runner import VectorBatchJobRunner, normalize_image_path

FEATURE_VECTOR_SIZE = DEFAULT_MODEL_SPEC.feature_vector_size

__all__ = [
    "DEFAULT_MODEL_FILENAME",
    "DEFAULT_MODEL_SPEC",
    "FEATURE_VECTOR_SIZE",
    "ExtractionCancelledError",
    "ExtractionTimeoutError",
    "ImageFeatureModelSpec",
    "ImageFeaturesExtractorError",
    "VectorBatchJobRunner",
    "WorkerCrashedError",
    "WorkerInitializationError",
    "get_model_spec",
    "normalize_image_path",
]
