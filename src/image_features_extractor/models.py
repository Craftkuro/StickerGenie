"""Public data contracts for image feature extraction jobs."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias

import numpy as np


FEATURE_VECTOR_SIZE = 768
ProviderSpec: TypeAlias = str | tuple[str, Mapping[str, Any]]


def _validate_non_negative_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ImageFeatureResult:
    """The feature extraction outcome for one input image."""

    image_path: str
    success: bool
    vector: np.ndarray | None
    error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.image_path, str) or not self.image_path:
            raise ValueError("image_path must be a non-empty string")
        if type(self.success) is not bool:
            raise ValueError("success must be a bool")

        if self.success:
            if not isinstance(self.vector, np.ndarray):
                raise ValueError("a successful result must contain a NumPy vector")
            if self.vector.shape != (FEATURE_VECTOR_SIZE,):
                raise ValueError(
                    f"a successful vector must have shape ({FEATURE_VECTOR_SIZE},)"
                )
            if self.vector.dtype != np.float32:
                raise ValueError("a successful vector must have dtype float32")
            if self.error is not None:
                raise ValueError("a successful result cannot contain an error")
            return

        if self.vector is not None:
            raise ValueError("a failed result cannot contain a vector")
        if not isinstance(self.error, str) or not self.error.strip():
            raise ValueError("a failed result must contain a non-empty error")

    @classmethod
    def succeeded(cls, image_path: str, vector: np.ndarray) -> ImageFeatureResult:
        return cls(image_path=image_path, success=True, vector=vector, error=None)

    @classmethod
    def failed(cls, image_path: str, error: str) -> ImageFeatureResult:
        return cls(image_path=image_path, success=False, vector=None, error=error)


@dataclass(frozen=True, slots=True)
class ExtractionProgress:
    """Monotonic image-level progress for an extraction job."""

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
class FeatureResultBatch:
    """One ordered result batch returned by the worker."""

    results: tuple[ImageFeatureResult, ...]
    progress: ExtractionProgress

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple):
            object.__setattr__(self, "results", tuple(self.results))
        if not self.results:
            raise ValueError("a result batch cannot be empty")
        if not all(isinstance(result, ImageFeatureResult) for result in self.results):
            raise ValueError("results must contain only ImageFeatureResult values")


@dataclass(frozen=True, slots=True)
class WorkerStartupInfo:
    """Information reported only after ONNX Runtime initialized successfully."""

    providers: tuple[str, ...]
    input_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.providers, tuple):
            object.__setattr__(self, "providers", tuple(self.providers))
        if not self.providers or not all(
            isinstance(provider, str) and provider for provider in self.providers
        ):
            raise ValueError("providers must contain at least one provider name")
        if not isinstance(self.input_name, str) or not self.input_name:
            raise ValueError("input_name must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ExtractionSummary:
    """Terminal statistics for a completed or cancelled job."""

    completed: int
    total: int | None
    succeeded: int
    failed: int
    providers: tuple[str, ...]
    duration_seconds: float
    cancelled: bool = False

    def __post_init__(self) -> None:
        ExtractionProgress(
            completed=self.completed,
            total=self.total,
            succeeded=self.succeeded,
            failed=self.failed,
        )
        if not isinstance(self.providers, tuple):
            object.__setattr__(self, "providers", tuple(self.providers))
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        if type(self.cancelled) is not bool:
            raise ValueError("cancelled must be a bool")


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    """Request object accepted by the Qt adapter."""

    image_paths: Iterable[str | os.PathLike[str]]
    model_path: str | os.PathLike[str]
    batch_size: int = 32
    total: int | None = None
    timeout: float | None = None
    providers: tuple[ProviderSpec, ...] | None = None
    cancel_grace_seconds: float = 1.0
