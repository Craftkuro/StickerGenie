"""
Image Features Extractor 模块

该模块提供基于多进程架构的图像特征提取功能,利用 ONNX 模型进行高效的并发处理。

主要组件:
- ImageFeaturesExtractor: 主要的管理类,协调多个 Worker 进程
- ExtractorSignals: PyQt6 信号集合,用于异步通知
- ExtractionTask: 任务数据类
- ExtractionResult: 结果数据类

使用示例:
    >>> from image_features_extractor import ImageFeaturesExtractor
    >>> extractor = ImageFeaturesExtractor(num_workers=2)
    >>> extractor.start()
    >>> features = extractor.extract_features_sync("image.jpg")
    >>> extractor.stop()

版本: 1.0
作者: StickerGenie Team
创建日期: 2025-01-14
"""

__version__ = "1.0.0"
__author__ = "StickerGenie Team"
__all__ = [
    # 主要类
    "ImageFeaturesExtractor",
    "ExtractorSignals",
    
    # 数据类
    "ExtractionTask",
    "ExtractionResult",
    "TaskStatus",
    
    # 异常类
    "ExtractorError",
    "ModelNotFoundError",
    "WorkerInitError",
    "TaskSubmissionError",
    "FeatureExtractionError",
    "WorkerCrashedError",
    "InvalidImageError",
    
    # GPU工具
    "GPUInfo",
    "detect_gpu_info",
    "get_gpu_provider_config",
    "print_gpu_info",
]

# 导入数据类
from .tasks import ExtractionTask, ExtractionResult, TaskStatus

# 导入异常类
from .exceptions import (
    ExtractorError,
    ModelNotFoundError,
    WorkerInitError,
    TaskSubmissionError,
    FeatureExtractionError,
    WorkerCrashedError,
    InvalidImageError,
)

# 导入主要类
from .extractor import ImageFeaturesExtractor
from .signals import ExtractorSignals

# 导入GPU工具
from .gpu_utils import (
    GPUInfo,
    detect_gpu_info,
    get_gpu_provider_config,
    print_gpu_info,
)