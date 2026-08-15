"""基于 batch_job_runner 的 OCR 批处理任务。"""

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
    """返回规范化后的绝对路径（不读取图片文件）。"""

    raw_path = os.fspath(image_path)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("image paths must be non-empty strings or path-like values")
    return str(Path(raw_path).expanduser().resolve(strict=False))


class OcrBatchJobRunner(BatchJobRunner):
    """对一组 blob 图片路径运行 OCR 批处理任务。

    OCR 是 CPU 密集型单线程工作，按已确认的设计使用 pool_size=1、
    batch_size=1，避免多个 OCR 引擎实例争夺 CPU。
    """

    def __init__(self, *, queue_size: int = 64, result_batch_size: int = 32):
        self._queue_size = queue_size
        self._result_batch_size = result_batch_size

    def build_pipeline(self) -> PipelineSpec:
        """声明 OCR 单阶段流水线：input -> ocr -> output。"""
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
        """逐批产出 OCR 结果；每条结果为 ``(path, text)`` 元组。"""

        return super().iter_results(
            (normalize_image_path(path) for path in items),
            **kwargs,
        )

    def run(
        self,
        items: Iterable[str | os.PathLike[str]],
        **kwargs: Any,
    ):
        """收集全部 OCR 结果并返回 JobSummary。"""

        return super().run(
            (normalize_image_path(path) for path in items),
            **kwargs,
        )
