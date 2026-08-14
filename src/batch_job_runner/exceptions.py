"""Job-level exceptions raised by batch job runners."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import JobSummary


class JobError(RuntimeError):
    """Base class for failures that affect an entire batch job."""


class WorkerInitializationError(JobError):
    """The worker could not initialize the pipeline (setup_func failed)."""


class WorkerCrashedError(JobError):
    """The worker exited without sending a terminal protocol message."""


class JobCancelledError(JobError):
    """The job was cancelled before normal completion."""

    def __init__(
        self,
        message: str,
        *,
        summary: "JobSummary | None" = None,
    ) -> None:
        super().__init__(message)
        self.summary = summary


class JobTimeoutError(JobError):
    """The job exceeded its configured timeout."""
