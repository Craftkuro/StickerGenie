"""Public data contracts for image text extraction jobs."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass


OCR_TEXT_PREFIX = "[OCR]"
OCR_TEXT_MAX_LENGTH = 4000


def _validate_non_negative_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ImageTextResult:
    """The OCR outcome for one input image."""

    image_path: str
    success: bool
    text: str | None
    error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.image_path, str) or not self.image_path:
            raise ValueError("image_path must be a non-empty string")
        if type(self.success) is not bool:
            raise ValueError("success must be a bool")

        if self.success:
            if self.error is not None:
                raise ValueError("a successful result cannot contain an error")
            if self.text is not None and (
                not isinstance(self.text, str)
                or not self.text.startswith(OCR_TEXT_PREFIX)
            ):
                raise ValueError(
                    "a successful text must be None or start with "
                    f"{OCR_TEXT_PREFIX!r}"
                )
            return

        if self.text is not None:
            raise ValueError("a failed result cannot contain text")
        if not isinstance(self.error, str) or not self.error.strip():
            raise ValueError("a failed result must contain a non-empty error")

    @classmethod
    def succeeded(
        cls,
        image_path: str,
        text: str | None = None,
    ) -> ImageTextResult:
        return cls(image_path=image_path, success=True, text=text, error=None)

    @classmethod
    def failed(cls, image_path: str, error: str) -> ImageTextResult:
        return cls(image_path=image_path, success=False, text=None, error=error)


@dataclass(frozen=True, slots=True)
class TextExtractionProgress:
    """Monotonic image-level progress for an OCR job."""

    completed: int
    total: int | None
    succeeded: int
    failed: int

    def __post_init__(self) -> None:
        _validate_non_negative_integer("completed", self.completed)
        _validate_non_negative_integer("succeeded", self.succeeded)
        _validate_non_negative_integer("failed", self.failed)
        if self.succeeded + self.failed != self.completed:
            raise ValueError("succeeded + failed must equal completed")
        if self.total is not None:
            _validate_non_negative_integer("total", self.total)
            if self.completed > self.total:
                raise ValueError("completed cannot exceed total")


@dataclass(frozen=True, slots=True)
class TextResultBatch:
    """One ordered result batch returned by the worker."""

    results: tuple[ImageTextResult, ...]
    progress: TextExtractionProgress

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple):
            object.__setattr__(self, "results", tuple(self.results))
        if not self.results:
            raise ValueError("a result batch cannot be empty")
        if not all(
            isinstance(result, ImageTextResult) for result in self.results
        ):
            raise ValueError(
                "results must contain only ImageTextResult values"
            )


@dataclass(frozen=True, slots=True)
class WorkerStartupInfo:
    """Information reported after RapidOCR initialized successfully."""

    engine_name: str = "onnxruntime"

    def __post_init__(self) -> None:
        if not isinstance(self.engine_name, str) or not self.engine_name:
            raise ValueError("engine_name must be a non-empty string")


@dataclass(frozen=True, slots=True)
class TextExtractionSummary:
    """Terminal statistics for a completed or cancelled OCR job."""

    completed: int
    total: int | None
    succeeded: int
    failed: int
    engine_name: str = "onnxruntime"
    duration_seconds: float = 0.0
    cancelled: bool = False

    def __post_init__(self) -> None:
        TextExtractionProgress(
            completed=self.completed,
            total=self.total,
            succeeded=self.succeeded,
            failed=self.failed,
        )
        if not isinstance(self.engine_name, str) or not self.engine_name:
            raise ValueError("engine_name must be a non-empty string")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        if type(self.cancelled) is not bool:
            raise ValueError("cancelled must be a bool")


@dataclass(frozen=True, slots=True)
class TextExtractionRequest:
    """Request object accepted by the Qt adapter."""

    image_paths: Iterable[str | os.PathLike[str]]
    batch_size: int = 8
    total: int | None = None
    timeout: float | None = None
    cancel_grace_seconds: float = 1.0
