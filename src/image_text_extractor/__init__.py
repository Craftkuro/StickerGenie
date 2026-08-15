"""基于 batch_job_runner 的图片 OCR 对外接口。

保留 OcrBatchJobRunner、文本拼接和路径规范化等必要导出；旧 extractor
的 worker/qt/同步接口已由 runner + stages 替代。
"""

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
