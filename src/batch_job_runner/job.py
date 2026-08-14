"""Parent-process batch job controller and synchronous runner API."""

from __future__ import annotations

import logging
import math
import multiprocessing as mp
import operator
import time
from collections.abc import Callable, Iterable, Iterator, Sized
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from .exceptions import (
    JobCancelledError,
    JobError,
    JobTimeoutError,
    WorkerCrashedError,
    WorkerInitializationError,
)
from .models import (
    GeneralDataWrapper,
    JobProgress,
    JobSummary,
    PipelineSpec,
    ResultBatch,
    validate_pipeline_spec,
)
from .scheduler import (
    CANCEL,
    DONE,
    END_INPUT,
    INIT_ERROR,
    INIT_OK,
    ITEMS,
    JOB_ERROR,
    RESULT_BATCH,
    scheduler_entry,
)


logger = logging.getLogger(__name__)
_POLL_INTERVAL_SECONDS = 0.05
_DEFAULT_SHUTDOWN_SECONDS = 1.0
_ITEMS_BATCH_SIZE = 32


def _validate_positive_integer(name: str, value: int) -> int:
    try:
        normalized = operator.index(value)
    except TypeError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if isinstance(value, bool) or normalized <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return normalized


def _normalize_total(
    items: Iterable[Any],
    total: int | None,
) -> int | None:
    if total is None and isinstance(items, Sized):
        total = len(items)
    if total is None:
        return None
    try:
        normalized = operator.index(total)
    except TypeError as error:
        raise ValueError("total must be a non-negative integer or None") from error
    if isinstance(total, bool) or normalized < 0:
        raise ValueError("total must be a non-negative integer or None")
    return normalized


def _normalize_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    normalized = float(timeout)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError("timeout must be greater than zero")
    return normalized


def _normalize_cancel_grace(seconds: float) -> float:
    normalized = float(seconds)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("cancel_grace_seconds cannot be negative")
    return normalized


@dataclass(frozen=True, slots=True)
class _JobEvent:
    kind: str
    payload: Any


class _BatchJob:
    """Non-blocking parent-side state machine for one worker process."""

    def __init__(
        self,
        spec: PipelineSpec,
        items: Iterable[Any],
        *,
        total: int | None,
        timeout: float | None,
        cancel_grace_seconds: float,
    ) -> None:
        self._spec = spec
        self._iterator = iter(items)
        self._total = total
        self._timeout = timeout
        self._cancel_grace_seconds = cancel_grace_seconds
        self._started_at = time.monotonic()
        self._deadline = (
            self._started_at + self._timeout
            if self._timeout is not None
            else None
        )
        self._cancel_deadline: float | None = None
        self._cancel_requested = False
        self._cancel_sent = False
        self._terminal = False
        self._input_exhausted = False
        self._submitted = 0
        self._completed = 0
        self._succeeded = 0
        self._failed = 0
        self._startup_info: Any = None
        self._received_init_ok = False

        context = mp.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        process = context.Process(
            target=scheduler_entry,
            args=(child_connection, spec),
            name="BatchJobRunnerWorker",
        )
        process.daemon = False

        self._connection: Connection = parent_connection
        self._process: mp.Process = process
        try:
            process.start()
        except BaseException:
            parent_connection.close()
            child_connection.close()
            raise
        finally:
            child_connection.close()

    @property
    def is_terminal(self) -> bool:
        return self._terminal

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def request_cancel(self) -> None:
        if self._terminal or self._cancel_requested:
            return
        self._cancel_requested = True
        self._cancel_deadline = (
            time.monotonic() + self._cancel_grace_seconds
        )
        self._send_cancel()

    def _send_cancel(self) -> None:
        if self._cancel_sent or self._terminal:
            return
        try:
            self._connection.send((CANCEL, None))
            self._cancel_sent = True
        except (BrokenPipeError, EOFError, OSError):
            pass

    def poll(self, timeout: float = 0.0) -> _JobEvent | None:
        if self._terminal:
            return None

        now = time.monotonic()
        self._check_timeout(now)
        if self._cancel_deadline is not None and now >= self._cancel_deadline:
            return self._finish_cancelled(force=True)

        wait_seconds = max(0.0, float(timeout))
        if self._deadline is not None:
            wait_seconds = min(wait_seconds, max(0.0, self._deadline - now))
        if self._cancel_deadline is not None:
            wait_seconds = min(
                wait_seconds,
                max(0.0, self._cancel_deadline - now),
            )

        try:
            has_message = self._connection.poll(wait_seconds)
        except (EOFError, OSError):
            has_message = False

        now = time.monotonic()
        self._check_timeout(now)
        if self._cancel_deadline is not None and now >= self._cancel_deadline:
            return self._finish_cancelled(force=True)

        if has_message:
            try:
                message = self._connection.recv()
            except (EOFError, OSError):
                return self._handle_worker_exit()
            event = self._handle_message(message)
            if event is not None:
                return event

        if not self._cancel_requested and not self._input_exhausted:
            self._maybe_send_next_batch()

        if not has_message and not self._process.is_alive():
            return self._handle_worker_exit()
        return None

    def _check_timeout(self, now: float) -> None:
        if self._deadline is None or now < self._deadline:
            return
        self._terminate_and_join()
        self._terminal = True
        raise JobTimeoutError(
            f"batch job timed out after {self._timeout:.3f} seconds"
        )

    def _handle_message(self, message: Any) -> _JobEvent | None:
        if (
            not isinstance(message, tuple)
            or len(message) != 2
            or not isinstance(message[0], str)
        ):
            self._fail_protocol(f"invalid worker IPC message: {message!r}")

        kind, payload = message
        if kind == INIT_OK:
            if self._received_init_ok:
                self._fail_protocol("duplicate INIT_OK message")
            self._received_init_ok = True
            self._startup_info = payload
            self._maybe_send_next_batch()
            return _JobEvent("started", payload)

        if kind == INIT_ERROR:
            self._terminate_and_join()
            self._terminal = True
            raise WorkerInitializationError(str(payload))

        if kind == RESULT_BATCH:
            return self._handle_result_batch(payload)

        if kind == JOB_ERROR:
            self._terminate_and_join()
            self._terminal = True
            raise JobError(str(payload))

        if kind == DONE:
            return self._handle_done(bool(payload))

        self._fail_protocol(f"unknown worker IPC message: {kind!r}")
        return None

    def _maybe_send_next_batch(self) -> None:
        if (
            self._terminal
            or self._cancel_requested
            or self._input_exhausted
            or not self._received_init_ok
        ):
            return
        try:
            self._send_next_items_batch()
        except (BrokenPipeError, EOFError, OSError):
            # The worker may have exited; the next poll converts this to a
            # WorkerCrashedError.
            pass

    def _send_next_items_batch(self) -> None:
        items: list[Any] = []
        try:
            for _ in range(_ITEMS_BATCH_SIZE):
                try:
                    item = next(self._iterator)
                except StopIteration:
                    break
                items.append(item)
        except BaseException:
            self._terminate_and_join()
            self._terminal = True
            raise

        if not items:
            if self._total is not None and self._submitted != self._total:
                self._terminate_and_join()
                self._terminal = True
                raise ValueError(
                    f"total={self._total} does not match the "
                    f"{self._submitted} input items"
                )
            self._input_exhausted = True
            self._connection.send((END_INPUT, None))
            return

        if self._total is not None and self._submitted + len(items) > self._total:
            self._terminate_and_join()
            self._terminal = True
            raise ValueError(f"input contains more items than total={self._total}")

        self._submitted += len(items)
        self._connection.send((ITEMS, tuple(items)))

    def _handle_result_batch(self, payload: Any) -> _JobEvent | None:
        if not self._received_init_ok:
            self._fail_protocol("worker sent results before INIT_OK")
        if not isinstance(payload, (tuple, list)) or not all(
            isinstance(result, GeneralDataWrapper) for result in payload
        ):
            self._fail_protocol("RESULT_BATCH contained invalid results")

        results = tuple(payload)
        self._completed += len(results)
        self._succeeded += sum(not result.hasException for result in results)
        self._failed += sum(result.hasException for result in results)

        if self._cancel_requested:
            return None

        self._maybe_send_next_batch()
        return _JobEvent(
            "batch",
            ResultBatch(
                results=results,
                progress=JobProgress(
                    completed=self._completed,
                    total=self._total,
                    succeeded=self._succeeded,
                    failed=self._failed,
                ),
            ),
        )

    def _handle_done(self, worker_cancelled: bool) -> _JobEvent:
        cancelled = self._cancel_requested or worker_cancelled
        if not cancelled:
            if not self._input_exhausted:
                self._fail_protocol(
                    "worker finished before all input was submitted"
                )
            if self._completed != self._submitted:
                self._fail_protocol(
                    "worker finished before returning all submitted results"
                )

        self._join_after_terminal_message()
        self._terminal = True
        summary = self._make_summary(cancelled=cancelled)
        return _JobEvent("cancelled" if cancelled else "finished", summary)

    def _handle_worker_exit(self) -> _JobEvent:
        self._process.join(timeout=_DEFAULT_SHUTDOWN_SECONDS)
        if self._process.is_alive():
            self._terminate_and_join()
        if self._cancel_requested:
            self._close_connection()
            self._terminal = True
            return _JobEvent("cancelled", self._make_summary(cancelled=True))

        exit_code = self._process.exitcode
        self._close_connection()
        self._terminal = True
        raise WorkerCrashedError(
            f"batch job worker exited unexpectedly with code {exit_code}"
        )

    def _finish_cancelled(self, *, force: bool) -> _JobEvent:
        if force:
            self._terminate_and_join()
        else:
            self._join_after_terminal_message()
        self._terminal = True
        return _JobEvent("cancelled", self._make_summary(cancelled=True))

    def _make_summary(self, *, cancelled: bool) -> JobSummary:
        return JobSummary(
            results=(),
            completed=self._completed,
            total=self._total,
            succeeded=self._succeeded,
            failed=self._failed,
            cancelled=cancelled,
            duration_seconds=time.monotonic() - self._started_at,
            startup_info=self._startup_info,
        )

    def _fail_protocol(self, message: str) -> None:
        self._terminate_and_join()
        self._terminal = True
        raise JobError(message)

    def _join_after_terminal_message(self) -> None:
        self._close_connection()
        self._process.join(timeout=_DEFAULT_SHUTDOWN_SECONDS)
        if self._process.is_alive():
            self._terminate_and_join()

    def _terminate_and_join(self) -> None:
        self._close_connection()
        if self._process.is_alive():
            self._process.terminate()
        self._process.join(timeout=_DEFAULT_SHUTDOWN_SECONDS)
        if self._process.is_alive():
            kill = getattr(self._process, "kill", None)
            if kill is not None:
                kill()
            self._process.join()

    def _close_connection(self) -> None:
        try:
            self._connection.close()
        except OSError:
            pass

    def close(self) -> None:
        if self._terminal:
            self._process.join(timeout=_DEFAULT_SHUTDOWN_SECONDS)
            if self._process.is_alive():
                self._terminate_and_join()
            self._close_connection()
            return
        self._terminate_and_join()
        self._terminal = True


class BatchJobRunner:
    """Base class for generic subprocess pipeline jobs.

    Subclasses implement :meth:`build_pipeline` to declare the worker-side
    pipeline and use :meth:`iter_results` / :meth:`run` to drive it. This
    class contains no Qt integration; callers run it synchronously.
    """

    def build_pipeline(self) -> PipelineSpec:
        raise NotImplementedError

    def iter_results(
        self,
        items: Iterable[Any],
        *,
        total: int | None = None,
        cancel_event: Any | None = None,
        progress: Callable[[JobProgress], None] | None = None,
        started: Callable[[Any], None] | None = None,
        timeout: float | None = None,
        cancel_grace_seconds: float = 1.0,
    ) -> Iterator[ResultBatch]:
        """Yield result batches while one worker processes the pipeline."""

        spec = self.build_pipeline()
        validate_pipeline_spec(spec)
        job = _BatchJob(
            spec,
            items,
            total=_normalize_total(items, total),
            timeout=_normalize_timeout(timeout),
            cancel_grace_seconds=_normalize_cancel_grace(
                cancel_grace_seconds
            ),
        )
        reported_progress = False
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    job.request_cancel()

                event = job.poll(_POLL_INTERVAL_SECONDS)
                if event is None:
                    continue
                if event.kind == "started":
                    if started is not None:
                        started(event.payload)
                    continue
                if event.kind == "batch":
                    batch: ResultBatch = event.payload
                    if progress is not None:
                        progress(batch.progress)
                    reported_progress = True
                    yield batch
                    continue
                if event.kind == "finished":
                    summary: JobSummary = event.payload
                    if progress is not None and not reported_progress:
                        progress(
                            JobProgress(
                                completed=summary.completed,
                                total=summary.total,
                                succeeded=summary.succeeded,
                                failed=summary.failed,
                            )
                        )
                    return
                if event.kind == "cancelled":
                    raise JobCancelledError(
                        "batch job was cancelled",
                        summary=event.payload,
                    )
                raise JobError(f"unknown job event: {event.kind!r}")
        finally:
            job.close()

    def run(
        self,
        items: Iterable[Any],
        *,
        total: int | None = None,
        cancel_event: Any | None = None,
        progress: Callable[[JobProgress], None] | None = None,
        started: Callable[[Any], None] | None = None,
        timeout: float | None = None,
        cancel_grace_seconds: float = 1.0,
    ) -> JobSummary:
        """Collect all results; a cancellation yields a cancelled summary."""

        results: list[GeneralDataWrapper] = []
        last_progress: JobProgress | None = None
        captured_startup: list[Any] = []
        started_at = time.monotonic()

        def _started(info: Any) -> None:
            captured_startup.append(info)
            if started is not None:
                started(info)

        try:
            for batch in self.iter_results(
                items,
                total=total,
                cancel_event=cancel_event,
                progress=progress,
                started=_started,
                timeout=timeout,
                cancel_grace_seconds=cancel_grace_seconds,
            ):
                results.extend(batch.results)
                last_progress = batch.progress
        except JobCancelledError as error:
            summary = error.summary
            if summary is not None:
                return JobSummary(
                    results=tuple(results),
                    completed=summary.completed,
                    total=summary.total,
                    succeeded=summary.succeeded,
                    failed=summary.failed,
                    cancelled=True,
                    duration_seconds=summary.duration_seconds,
                    startup_info=(
                        captured_startup[0] if captured_startup else None
                    ),
                )
            return JobSummary(
                results=tuple(results),
                completed=0,
                total=total,
                succeeded=0,
                failed=0,
            cancelled=True,
            duration_seconds=time.monotonic() - started_at,
            startup_info=(
                captured_startup[0] if captured_startup else None
            ),
        )

        if last_progress is None:
            progress_value = JobProgress(
                completed=0,
                total=_normalize_total(items, total),
                succeeded=0,
                failed=0,
            )
        else:
            progress_value = last_progress
        return JobSummary(
            results=tuple(results),
            completed=progress_value.completed,
            total=progress_value.total,
            succeeded=progress_value.succeeded,
            failed=progress_value.failed,
            cancelled=False,
            duration_seconds=time.monotonic() - started_at,
            startup_info=(
                captured_startup[0] if captured_startup else None
            ),
        )
