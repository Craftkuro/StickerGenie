"""OCR 批处理任务的 job 级异常。

这里只是 batch_job_runner 通用异常的兼容别名，让调用方继续使用 OCR 相关
命名，底层实现已经完全共用。
"""

from batch_job_runner.exceptions import (
    JobCancelledError,
    JobError,
    JobTimeoutError,
    WorkerCrashedError,
    WorkerInitializationError,
)

ImageTextExtractorError = JobError
TextExtractionCancelledError = JobCancelledError
TextExtractionTimeoutError = JobTimeoutError
