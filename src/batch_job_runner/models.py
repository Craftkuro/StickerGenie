"""Data contracts for generic subprocess batch job pipelines."""

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
    """A single pipeline item flowing through queues and result batches.

    ``data`` holds the user payload. Failed wrappers additionally carry an
    error string in ``"TypeName: message"`` format plus the failing stage name.
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
    """Return the original input identifier carried by a result wrapper.

    Stage data is expected to carry the input identifier (for example an image
    path) either directly or as the first element of a tuple, so callers can
    map results back even when the output is not the raw input.
    """

    data = wrapper.data
    if isinstance(data, tuple) and data:
        return data[0]
    return data


@dataclass(frozen=True, slots=True)
class QueueSpec:
    """One bounded pipeline queue used for backpressure and memory control."""

    name: str
    maxsize: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("queue name must be a non-empty string")
        _validate_positive_integer("queue maxsize", self.maxsize)


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One pipeline stage executed by ``pool_size`` worker threads."""

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
    """Complete pipeline definition shared with the worker process."""

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
    """Validate queue uniqueness and stage chaining rules."""

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
    """Monotonic item-level progress for a batch job."""

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
    """One batch of final pipeline results plus monotonic progress."""

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
    """Terminal statistics for a completed or cancelled batch job."""

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
