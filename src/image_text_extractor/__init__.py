"""Short-lived, single-worker image text extraction."""

from typing import TYPE_CHECKING

from .exceptions import (
    ImageTextExtractorError,
    TextExtractionCancelledError,
    TextExtractionTimeoutError,
    WorkerCrashedError,
    WorkerInitializationError,
)
from .extractor import extract_texts, iter_texts, normalize_image_path
from .models import (
    OCR_TEXT_MAX_LENGTH,
    OCR_TEXT_PREFIX,
    ImageTextResult,
    TextExtractionProgress,
    TextExtractionRequest,
    TextExtractionSummary,
    TextResultBatch,
    WorkerStartupInfo,
)
from .worker import compose_ocr_text

if TYPE_CHECKING:
    from .qt import QtImageTextExtractor

__all__ = [
    "OCR_TEXT_MAX_LENGTH",
    "OCR_TEXT_PREFIX",
    "ImageTextExtractorError",
    "ImageTextResult",
    "QtImageTextExtractor",
    "TextExtractionCancelledError",
    "TextExtractionProgress",
    "TextExtractionRequest",
    "TextExtractionSummary",
    "TextExtractionTimeoutError",
    "TextResultBatch",
    "WorkerCrashedError",
    "WorkerInitializationError",
    "WorkerStartupInfo",
    "compose_ocr_text",
    "extract_texts",
    "iter_texts",
    "normalize_image_path",
]


def __getattr__(name: str):
    if name == "QtImageTextExtractor":
        from .qt import QtImageTextExtractor

        return QtImageTextExtractor
    raise AttributeError(name)
