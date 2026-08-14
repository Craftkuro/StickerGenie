"""OCR batch job runner built on :mod:`batch_job_runner`."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from batch_job_runner.job import BatchJobRunner
from batch_job_runner.models import (
    PipelineSpec,
    QueueSpec,
    ResultBatch,
    StageSpec,
)

from .stages import load_ocr_engine, ocr_image


def normalize_image_path(image_path: str | os.PathLike[str]) -> str:
    """Return a normalized absolute path without touching the image file."""

    raw_path = os.fspath(image_path)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("image paths must be non-empty strings or path-like values")
    return str(Path(raw_path).expanduser().resolve(strict=False))


class OcrBatchJobRunner(BatchJobRunner):
    """Run one OCR batch job over blob paths.

    OCR is CPU-bound single-threaded work; per the confirmed design the
    pipeline uses ``pool_size=1`` and ``batch_size=1``.
    """

    def __init__(self, *, queue_size: int = 64, result_batch_size: int = 32):
        self._queue_size = queue_size
        self._result_batch_size = result_batch_size

    def build_pipeline(self) -> PipelineSpec:
        return PipelineSpec(
            queues=(
                QueueSpec("input", self._queue_size),
                QueueSpec("output", self._queue_size),
            ),
            stages=(
                StageSpec(
                    "ocr",
                    "input",
                    "output",
                    ocr_image,
                    pool_size=1,
                    batch_size=1,
                ),
            ),
            setup_func=load_ocr_engine,
            result_batch_size=self._result_batch_size,
        )

    def iter_results(
        self,
        items: Iterable[str | os.PathLike[str]],
        **kwargs: Any,
    ) -> Iterator[ResultBatch]:
        """Yield OCR result batches; results carry ``(path, text)`` data."""

        return super().iter_results(
            (normalize_image_path(path) for path in items),
            **kwargs,
        )

    def run(
        self,
        items: Iterable[str | os.PathLike[str]],
        **kwargs: Any,
    ):
        """Collect all OCR results into a :class:`JobSummary`."""

        return super().run(
            (normalize_image_path(path) for path in items),
            **kwargs,
        )
