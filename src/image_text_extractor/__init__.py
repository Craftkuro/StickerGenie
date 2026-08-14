"""Short-lived batch OCR jobs built on :mod:`batch_job_runner`."""

from .exceptions import (
    ImageTextExtractorError,
    TextExtractionCancelledError,
    TextExtractionTimeoutError,
    WorkerCrashedError,
    WorkerInitializationError,
)
from .runner import OcrBatchJobRunner, normalize_image_path
from .stages import (
    OCR_TEXT_MAX_LENGTH,
    OCR_TEXT_PREFIX,
    compose_ocr_text,
)

__all__ = [
    "OCR_TEXT_MAX_LENGTH",
    "OCR_TEXT_PREFIX",
    "ImageTextExtractorError",
    "OcrBatchJobRunner",
    "TextExtractionCancelledError",
    "TextExtractionTimeoutError",
    "WorkerCrashedError",
    "WorkerInitializationError",
    "compose_ocr_text",
    "normalize_image_path",
]
