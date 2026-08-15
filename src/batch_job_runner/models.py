"""通用子进程批处理流水线的数据契约。

流水线中的队列统一流转 GeneralDataWrapper；父进程与子进程之间通过
ResultBatch / JobProgress / JobSummary 传递结果与进度。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def _validate_non_negative_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_positive_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class GeneralDataWrapper:
    """队列与结果批次中流转的单条数据。

    成功时 data 保存用户函数输出；失败时保存本步输入，并携带
    "TypeName: message" 格式的错误串和出错 stage_name。失败 wrapper 会原样
    穿过后续所有步骤，不再进入任何用户函数，保证每条输入恰好产生一个结果。
    """

    data: Any
    hasException: bool = False
    error: str | None = None
    stage_name: str | None = None

    def __post_init__(self) -> None:
        if type(self.hasException) is not bool:
            raise ValueError("hasException must be a bool")
        if self.hasException:
            if not isinstance(self.error, str) or not self.error.strip():
                raise ValueError(
                    "a failed wrapper must contain a non-empty error"
                )
            if not isinstance(self.stage_name, str) or not self.stage_name:
                raise ValueError(
                    "a failed wrapper must contain a stage_name"
                )
            return
        if self.error is not None:
            raise ValueError("a successful wrapper cannot contain an error")
        if self.stage_name is not None:
            raise ValueError(
                "a successful wrapper cannot contain a stage_name"
            )


def wrapper_input_identifier(wrapper: GeneralDataWrapper) -> Any:
    """从结果 wrapper 中取回原始输入标识。

    各步骤约定把输入标识（例如图片路径）放在 data 本身或元组首元素，调用方
    即使拿到的是转换后的数据，也能据此把结果映射回原始输入。
    """

    data = wrapper.data
    if isinstance(data, tuple) and data:
        return data[0]
    return data


@dataclass(frozen=True, slots=True)
class QueueSpec:
    """一个有界流水线队列，用于背压和内存控制。"""

    name: str
    maxsize: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("queue name must be a non-empty string")
        _validate_positive_integer("queue maxsize", self.maxsize)


@dataclass(frozen=True, slots=True)
class StageSpec:
    """一个由 pool_size 个线程并发执行的流水线步骤。

    batch_size > 1 时，func 接收列表并返回等长列表；worker 会尽量凑满一批，
    输入流末尾不足一批时按实际数量处理。
    """

    name: str
    input_queue: str
    output_queue: str
    func: Callable
    pool_size: int
    batch_size: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("stage name must be a non-empty string")
        if not callable(self.func):
            raise ValueError("stage func must be callable")
        _validate_positive_integer("pool_size", self.pool_size)
        _validate_positive_integer("batch_size", self.batch_size)


@dataclass(frozen=True, slots=True)
class PipelineSpec:
    """发给子进程的完整流水线定义。

    setup_func 在子进程内只执行一次，用于加载模型等惰性单例；返回值随
    INIT_OK 返回父进程，可作为启动信息展示。
    """

    queues: tuple[QueueSpec, ...]
    stages: tuple[StageSpec, ...]
    setup_func: Callable | None = None
    result_batch_size: int = 32

    def __post_init__(self) -> None:
        if not isinstance(self.queues, tuple):
            object.__setattr__(self, "queues", tuple(self.queues))
        if not isinstance(self.stages, tuple):
            object.__setattr__(self, "stages", tuple(self.stages))
        if self.setup_func is not None and not callable(self.setup_func):
            raise ValueError("setup_func must be callable or None")
        _validate_positive_integer("result_batch_size", self.result_batch_size)


def validate_pipeline_spec(spec: PipelineSpec) -> None:
    """校验队列唯一性、stage 参数合法性以及队列首尾相接的链式结构。"""

    if not isinstance(spec, PipelineSpec):
        raise TypeError("spec must be a PipelineSpec")
    if not spec.queues:
        raise ValueError("pipeline must declare at least one queue")
    if not spec.stages:
        raise ValueError("pipeline must declare at least one stage")

    queue_names = [queue.name for queue in spec.queues]
    if len(set(queue_names)) != len(queue_names):
        raise ValueError("queue names must be unique")

    stage_names = [stage.name for stage in spec.stages]
    if len(set(stage_names)) != len(stage_names):
        raise ValueError("stage names must be unique")

    for stage in spec.stages:
        if stage.input_queue not in queue_names:
            raise ValueError(
                f"stage {stage.name!r} references unknown input queue "
                f"{stage.input_queue!r}"
            )
        if stage.output_queue not in queue_names:
            raise ValueError(
                f"stage {stage.name!r} references unknown output queue "
                f"{stage.output_queue!r}"
            )

    if spec.stages[0].input_queue != queue_names[0]:
        raise ValueError("the first stage must read from the first queue")
    if spec.stages[-1].output_queue != queue_names[-1]:
        raise ValueError("the last stage must write to the last queue")
    for index in range(len(spec.stages) - 1):
        if spec.stages[index].output_queue != spec.stages[index + 1].input_queue:
            raise ValueError(
                f"stage {spec.stages[index].name!r} does not chain into "
                f"stage {spec.stages[index + 1].name!r}"
            )


@dataclass(frozen=True, slots=True)
class JobProgress:
    """单调递增的条数级任务进度。

    completed 必须等于 succeeded + failed，且不超过 total，保证进度口径一致。
    """

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
class ResultBatch:
    """一批最终流水线结果及其对应的单调进度。"""

    results: tuple[GeneralDataWrapper, ...]
    progress: JobProgress

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple):
            object.__setattr__(self, "results", tuple(self.results))
        if not self.results:
            raise ValueError("a result batch cannot be empty")
        if not all(
            isinstance(result, GeneralDataWrapper)
            for result in self.results
        ):
            raise ValueError(
                "results must contain only GeneralDataWrapper values"
            )


@dataclass(frozen=True, slots=True)
class JobSummary:
    """任务正常结束或取消后的统计摘要，包含全部结果与错误计数。"""

    results: tuple[GeneralDataWrapper, ...]
    completed: int
    succeeded: int
    failed: int
    cancelled: bool
    duration_seconds: float
    total: int | None = None
    startup_info: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple):
            object.__setattr__(self, "results", tuple(self.results))
        JobProgress(
            completed=self.completed,
            total=self.total,
            succeeded=self.succeeded,
            failed=self.failed,
        )
        if type(self.cancelled) is not bool:
            raise ValueError("cancelled must be a bool")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
