"""基于 batch_job_runner 的 OCR 批处理任务。"""

from __future__ import annotations

import math
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


def default_ocr_pool_size() -> int:
    """默认 stage 线程池大小：⌈逻辑核数/4⌉，上限 8。

    每个 worker 线程各载一套引擎，其 ORT 会话固定用最多 4 个 intra-op
    线程（见 ppocr_lite.sessions），总线程约等于逻辑核数；worker 进程为
    低于正常优先级（batch_job_runner.job），轻微超订不会抢占 UI。
    超过 8 个 worker 后总线程 >32，Python 后处理段的 GIL 串行化成为瓶颈，
    继续增加 worker 收益递减，故封顶。
    """

    return max(1, min(8, math.ceil((os.cpu_count() or 1) / 4)))


class OcrBatchJobRunner(BatchJobRunner):
    """对一组 blob 图片路径运行 OCR 批处理任务。

    OCR stage 默认使用 default_ocr_pool_size() 个 worker 线程、每线程一套
    引擎实例（三个模型共 <30MB）；batch_size=1 逐图识别。
    """

    def __init__(
        self,
        *,
        queue_size: int = 64,
        result_batch_size: int = 32,
        pool_size: int | None = None,
    ):
        self._queue_size = queue_size
        self._result_batch_size = result_batch_size
        self._pool_size = (
            pool_size if pool_size is not None else default_ocr_pool_size()
        )

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
                    pool_size=self._pool_size,
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
