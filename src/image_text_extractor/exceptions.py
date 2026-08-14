"""Job-level exceptions raised by OCR batch jobs.

These are aliases of the generic :mod:`batch_job_runner` exceptions so that
callers can keep feature-specific names while the underlying implementation is
shared.
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
