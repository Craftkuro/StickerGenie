"""基于 batch_job_runner 的图片特征向量批处理任务。

VectorBatchJobRunner 声明“预处理 -> 批量推理”两阶段流水线，父进程只传图片
路径，子进程自行读图、预处理和加载 ONNX 模型。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from batch_job_runner.job import BatchJobRunner
from batch_job_runner.models import (
    PipelineSpec,
    QueueSpec,
    ResultBatch,
    StageSpec,
)

from .stages import ProviderSpec, load_session, preprocess_image, run_batch_inference


def normalize_image_path(image_path: str | os.PathLike[str]) -> str:
    """返回规范化后的绝对路径（不读取图片文件）。"""

    raw_path = os.fspath(image_path)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("image paths must be non-empty strings or path-like values")
    return str(Path(raw_path).expanduser().resolve(strict=False))


def _normalize_providers(
    providers: Sequence[ProviderSpec] | None,
) -> tuple[ProviderSpec, ...] | None:
    """把用户传入的 provider 配置统一为可 pickle 的元组序列。"""
    if providers is None:
        return None
    if not providers:
        raise ValueError("providers cannot be empty")

    normalized: list[ProviderSpec] = []
    for provider in providers:
        if isinstance(provider, str) and provider:
            normalized.append(provider)
        elif (
            isinstance(provider, tuple)
            and len(provider) == 2
            and isinstance(provider[0], str)
            and provider[0]
            and isinstance(provider[1], Mapping)
        ):
            normalized.append((provider[0], dict(provider[1])))
        else:
            raise ValueError(f"invalid provider configuration: {provider!r}")
    return tuple(normalized)


class VectorBatchJobRunner(BatchJobRunner):
    """对一组 blob 图片路径运行特征向量批处理任务。

    第一阶段用 CPU 线程池并行预处理图片；第二阶段用单线程批量跑 ONNX
    推理，避免 onnxruntime 内部线程与流水线线程叠加导致超订。
    """

    def __init__(
        self,
        model_path: str | os.PathLike[str],
        *,
        providers: Sequence[ProviderSpec] | None = None,
        preprocess_pool_size: int = 4,
        infer_pool_size: int = 1,
        infer_batch_size: int = 32,
        queue_size: int = 64,
        preprocessed_queue_size: int = 16,
        inferred_queue_size: int = 8,
        result_batch_size: int = 32,
    ) -> None:
        self._model_path = normalize_image_path(model_path)
        self._providers = _normalize_providers(providers)
        self._preprocess_pool_size = preprocess_pool_size
        self._infer_pool_size = infer_pool_size
        self._infer_batch_size = infer_batch_size
        self._queue_size = queue_size
        self._preprocessed_queue_size = preprocessed_queue_size
        self._inferred_queue_size = inferred_queue_size
        self._result_batch_size = result_batch_size

    def build_pipeline(self) -> PipelineSpec:
        """声明向量流水线：input -> preprocess -> infer -> inferred。"""
        return PipelineSpec(
            queues=(
                QueueSpec("input", self._queue_size),
                QueueSpec("preprocessed", self._preprocessed_queue_size),
                QueueSpec("inferred", self._inferred_queue_size),
            ),
            stages=(
                StageSpec(
                    "preprocess",
                    "input",
                    "preprocessed",
                    preprocess_image,
                    pool_size=self._preprocess_pool_size,
                ),
                StageSpec(
                    "infer",
                    "preprocessed",
                    "inferred",
                    run_batch_inference,
                    pool_size=self._infer_pool_size,
                    batch_size=self._infer_batch_size,
                ),
            ),
            setup_func=partial(
                load_session,
                model_path=self._model_path,
                providers=self._providers,
            ),
            result_batch_size=self._result_batch_size,
        )

    def iter_results(
        self,
        items: Iterable[str | os.PathLike[str]],
        **kwargs: Any,
    ) -> Iterator[ResultBatch]:
        """逐批产出向量结果；每条结果为 ``(path, vector)`` 元组。"""

        return super().iter_results(
            (normalize_image_path(path) for path in items),
            **kwargs,
        )

    def run(
        self,
        items: Iterable[str | os.PathLike[str]],
        **kwargs: Any,
    ):
        """收集全部向量结果并返回 JobSummary。"""

        return super().run(
            (normalize_image_path(path) for path in items),
            **kwargs,
        )
