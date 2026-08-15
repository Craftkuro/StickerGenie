"""基于 batch_job_runner 的图片特征向量提取对外接口。

保留模型规格、路径规范化与 VectorBatchJobRunner 等必要导出；旧 extractor
的 worker/qt/同步接口已由 runner + stages 替代。
"""

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
