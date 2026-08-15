"""通用子进程流水线任务调度。

OCR 与向量生成等批处理任务通过声明 PipelineSpec 复用同一套实现：
父进程侧由 BatchJobRunner 管理进程生命周期和 IPC，子进程侧由 scheduler
驱动多阶段队列流水线。本模块不依赖 Qt，调用方负责在后台线程中同步使用。
"""

from .exceptions import (
    JobCancelledError,
    JobError,
    JobTimeoutError,
    WorkerCrashedError,
    WorkerInitializationError,
)
from .job import BatchJobRunner
from .models import (
    GeneralDataWrapper,
    JobProgress,
    JobSummary,
    PipelineSpec,
    QueueSpec,
    ResultBatch,
    StageSpec,
    validate_pipeline_spec,
    wrapper_input_identifier,
)

__all__ = [
    "BatchJobRunner",
    "GeneralDataWrapper",
    "JobCancelledError",
    "JobError",
    "JobProgress",
    "JobSummary",
    "JobTimeoutError",
    "PipelineSpec",
    "QueueSpec",
    "ResultBatch",
    "StageSpec",
    "WorkerCrashedError",
    "WorkerInitializationError",
    "validate_pipeline_spec",
    "wrapper_input_identifier",
]
