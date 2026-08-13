"""Parent-process controller and synchronous text extraction APIs."""

from __future__ import annotations

import logging
import math
import multiprocessing as mp
import operator
import os
import time
from collections.abc import Callable, Iterable, Iterator, Sized
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from .exceptions import (
    ImageTextExtractorError,
    TextExtractionCancelledError,
    TextExtractionTimeoutError,
    WorkerCrashedError,
    WorkerInitializationError,
)
from .models import (
    TextExtractionProgress,
    TextExtractionSummary,
    TextResultBatch,
    ImageTextResult,
    WorkerStartupInfo,
)
from .worker import (
    BATCH_RESULT,
    CANCEL,
    DONE,
    END_INPUT,
    INIT_ERROR,
    INIT_OK,
    JOB_ERROR,
    PROCESS_BATCH,
    REQUEST_BATCH,
    worker_process_entry,
)


logger = logging.getLogger(__name__)
_POLL_INTERVAL_SECONDS = 0.05
_DEFAULT_SHUTDOWN_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class _JobEvent:
    kind: str
    payload: Any


def normalize_image_path(image_path: str | os.PathLike[str]) -> str:
    """Return a normalized absolute path without touching the image file."""

    raw_path = os.fspath(image_path)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("image paths must be non-empty strings or path-like values")
    return str(Path(raw_path).expanduser().resolve(strict=False))


def _validate_positive_integer(name: str, value: int) -> int:
    try:
        normalized = operator.index(value)
    except TypeError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if isinstance(value, bool) or normalized <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return normalized


def _normalize_total(
    image_paths: Iterable[str | os.PathLike[str]],
    total: int | None,
) -> int | None:
    if total is None and isinstance(image_paths, Sized):
        total = len(image_paths)
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


class _TextExtractionJob:
    """Non-blocking state machine shared by sync and Qt adapters."""

    def __init__(
        self,
        image_paths: Iterable[str | os.PathLike[str]],
        *,
        batch_size: int,
        total: int | None,
        timeout: float | None,
        cancel_grace_seconds: float = _DEFAULT_SHUTDOWN_SECONDS,
    ) -> None:
        self.batch_size = _validate_positive_integer("batch_size", batch_size)
        self.total = _normalize_total(image_paths, total)
        self.timeout = _normalize_timeout(timeout)
        self.cancel_grace_seconds = float(cancel_grace_seconds)
        if (
            not math.isfinite(self.cancel_grace_seconds)
            or self.cancel_grace_seconds < 0
        ):
            raise ValueError("cancel_grace_seconds cannot be negative")

        self._iterator = iter(image_paths)
        self._started_at = time.monotonic()
        self._deadline = (
            self._started_at + self.timeout if self.timeout is not None else None
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
        self._inflight_paths: tuple[str, ...] | None = None
        self._startup_info: WorkerStartupInfo | None = None

        context = mp.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        process = context.Process(
            target=worker_process_entry,
            args=(child_connection,),
            name="ImageTextExtractorWorker",
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
        self._cancel_deadline = time.monotonic() + self.cancel_grace_seconds
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
            return self._handle_message(message)

        if not self._process.is_alive():
            return self._handle_worker_exit()
        return None

    def _check_timeout(self, now: float) -> None:
        if self._deadline is None or now < self._deadline:
            return
        self._terminate_and_join()
        self._terminal = True
        raise TextExtractionTimeoutError(
            f"image text extraction timed out after {self.timeout:.3f} seconds"
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
            if self._startup_info is not None or not isinstance(
                payload,
                WorkerStartupInfo,
            ):
                self._fail_protocol("invalid or duplicate INIT_OK message")
            self._startup_info = payload
            logger.info(
                "image text worker initialized with %s",
                payload.engine_name,
            )
            return _JobEvent("started", payload)

        if kind == INIT_ERROR:
            self._terminate_and_join()
            self._terminal = True
            raise WorkerInitializationError(str(payload))

        if kind == REQUEST_BATCH:
            if self._startup_info is None:
                self._fail_protocol("worker requested input before INIT_OK")
            if self._inflight_paths is not None or self._input_exhausted:
                # 预取已满足该请求，或输入已耗尽；无需再次下发。
                return None
            self._send_next_batch()
            return None

        if kind == BATCH_RESULT:
            return self._handle_batch_result(payload)

        if kind == JOB_ERROR:
            self._terminate_and_join()
            self._terminal = True
            raise ImageTextExtractorError(str(payload))

        if kind == DONE:
            return self._handle_done(bool(payload))

        self._fail_protocol(f"unknown worker IPC message: {kind!r}")
        return None

    def _send_next_batch(self) -> None:
        if self._cancel_requested:
            self._send_cancel()
            return

        paths: list[str] = []
        try:
            for _ in range(self.batch_size):
                try:
                    path = next(self._iterator)
                except StopIteration:
                    break
                paths.append(normalize_image_path(path))
        except BaseException:
            self._terminate_and_join()
            self._terminal = True
            raise

        if not paths:
            if self.total is not None and self._submitted != self.total:
                self._terminate_and_join()
                self._terminal = True
                raise ValueError(
                    f"total={self.total} does not match the "
                    f"{self._submitted} input paths"
                )
            self._input_exhausted = True
            self._connection.send((END_INPUT, None))
            return

        if self.total is not None and self._submitted + len(paths) > self.total:
            self._terminate_and_join()
            self._terminal = True
            raise ValueError(f"input contains more paths than total={self.total}")

        self._submitted += len(paths)
        self._inflight_paths = tuple(paths)
        self._connection.send((PROCESS_BATCH, self._inflight_paths))

    def _handle_batch_result(self, payload: Any) -> _JobEvent | None:
        if self._inflight_paths is None:
            self._fail_protocol(
                "worker returned a batch without an in-flight request"
            )
        if not isinstance(payload, (tuple, list)) or not all(
            isinstance(result, ImageTextResult) for result in payload
        ):
            self._fail_protocol("BATCH_RESULT contained invalid results")

        results = tuple(payload)
        if len(results) != len(self._inflight_paths):
            self._fail_protocol("BATCH_RESULT length did not match the input batch")
        if tuple(result.image_path for result in results) != self._inflight_paths:
            self._fail_protocol("BATCH_RESULT did not preserve input path order")

        self._inflight_paths = None
        self._completed += len(results)
        self._succeeded += sum(result.success for result in results)
        self._failed += sum(not result.success for result in results)
        progress = TextExtractionProgress(
            completed=self._completed,
            total=self.total,
            succeeded=self._succeeded,
            failed=self._failed,
        )
        if self._cancel_requested:
            return None
        self._prefetch_next_batch()
        return _JobEvent(
            "batch",
            TextResultBatch(results=results, progress=progress),
        )

    def _prefetch_next_batch(self) -> None:
        """在下游消费当前批次前，先把下一批下发给 Worker，隐藏写库开销。"""
        if self._input_exhausted:
            return
        try:
            self._send_next_batch()
        except (BrokenPipeError, EOFError, OSError):
            # Worker 可能已退出；由后续 poll 检测并转换为 WorkerCrashedError。
            pass

    def _handle_done(self, worker_cancelled: bool) -> _JobEvent:
        cancelled = self._cancel_requested or worker_cancelled
        if not cancelled:
            if not self._input_exhausted:
                self._fail_protocol("worker finished before all input was submitted")
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
            f"image text worker exited unexpectedly with code {exit_code}"
        )

    def _finish_cancelled(self, *, force: bool) -> _JobEvent:
        if force:
            self._terminate_and_join()
        else:
            self._join_after_terminal_message()
        self._terminal = True
        return _JobEvent("cancelled", self._make_summary(cancelled=True))

    def _make_summary(self, *, cancelled: bool) -> TextExtractionSummary:
        engine_name = (
            self._startup_info.engine_name
            if self._startup_info is not None
            else "onnxruntime"
        )
        return TextExtractionSummary(
            completed=self._completed,
            total=self.total,
            succeeded=self._succeeded,
            failed=self._failed,
            engine_name=engine_name,
            duration_seconds=time.monotonic() - self._started_at,
            cancelled=cancelled,
        )

    def _fail_protocol(self, message: str) -> None:
        self._terminate_and_join()
        self._terminal = True
        raise ImageTextExtractorError(message)

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


def iter_texts(
    image_paths: Iterable[str | os.PathLike[str]],
    *,
    batch_size: int = 8,
    total: int | None = None,
    progress: Callable[[TextExtractionProgress], None] | None = None,
    started: Callable[[WorkerStartupInfo], None] | None = None,
    timeout: float | None = None,
    cancel_event: Any | None = None,
) -> Iterator[TextResultBatch]:
    """Yield ordered result batches while one short-lived worker processes paths."""

    job = _TextExtractionJob(
        image_paths,
        batch_size=batch_size,
        total=total,
        timeout=timeout,
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
                batch: TextResultBatch = event.payload
                if progress is not None:
                    progress(batch.progress)
                reported_progress = True
                yield batch
                continue
            if event.kind == "finished":
                summary: TextExtractionSummary = event.payload
                if progress is not None and not reported_progress:
                    progress(
                        TextExtractionProgress(
                            completed=summary.completed,
                            total=summary.total,
                            succeeded=summary.succeeded,
                            failed=summary.failed,
                        )
                    )
                return
            if event.kind == "cancelled":
                raise TextExtractionCancelledError(
                    "image text extraction was cancelled"
                )
            raise ImageTextExtractorError(f"unknown job event: {event.kind!r}")
    finally:
        job.close()


def extract_texts(
    image_paths: Iterable[str | os.PathLike[str]],
    *,
    batch_size: int = 8,
    total: int | None = None,
    progress: Callable[[TextExtractionProgress], None] | None = None,
    started: Callable[[WorkerStartupInfo], None] | None = None,
    timeout: float | None = None,
    cancel_event: Any | None = None,
) -> list[ImageTextResult]:
    """Collect all ordered image results from a short-lived OCR job."""

    results: list[ImageTextResult] = []
    for batch in iter_texts(
        image_paths,
        batch_size=batch_size,
        total=total,
        progress=progress,
        started=started,
        timeout=timeout,
        cancel_event=cancel_event,
    ):
        results.extend(batch.results)
    return results
