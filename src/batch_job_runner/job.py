"""父进程侧批处理任务控制器与同步调用 API。

BatchJobRunner 负责启动/回收子进程、维护 IPC 状态机，并把结果批次逐批交给
调用方；迭代式接口 iter_results 与收集式接口 run 都基于同一状态机实现。
"""

from __future__ import annotations

import ctypes
import logging
import math
import multiprocessing as mp
import operator
import sys
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
    REQUEST_INPUT,
    RESULT_BATCH,
    scheduler_entry,
)


logger = logging.getLogger(__name__)
_POLL_INTERVAL_SECONDS = 0.05
_DEFAULT_SHUTDOWN_SECONDS = 1.0
# 父进程每批最多下发 32 条输入；批量下发降低 IPC 次数，同时让管道和子进程
# 首队列中的在途数据保持有界。
_ITEMS_BATCH_SIZE = 32
# Windows BELOW_NORMAL_PRIORITY_CLASS，避免批处理 worker 抢占 UI。
_WIN32_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
# SetPriorityClass 需要的最小进程访问权限。
_PROCESS_SET_INFORMATION = 0x0200


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


def _apply_worker_priority(process: mp.Process) -> None:
    """Windows 下设置 worker 进程的优先级类；其他平台忽略。

    优先级调整属于尽力而为：失败只记录日志，不让批处理任务本身报错。
    """
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = (
            ctypes.c_uint32,
            ctypes.c_bool,
            ctypes.c_uint32,
        )
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        kernel32.SetPriorityClass.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool

        handle = kernel32.OpenProcess(
            _PROCESS_SET_INFORMATION,
            False,
            process.pid,
        )
        if not handle:
            logger.warning(
                "failed to open worker process %s for priority change: %s",
                process.pid,
                ctypes.WinError(),
            )
            return
        try:
            if not kernel32.SetPriorityClass(
                handle,
                _WIN32_BELOW_NORMAL_PRIORITY_CLASS,
            ):
                logger.warning(
                    "failed to set worker %s to below-normal priority: %s",
                    process.pid,
                    ctypes.WinError(),
                )
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        logger.warning(
            "could not set worker process to below-normal priority",
            exc_info=True,
        )


@dataclass(frozen=True, slots=True)
class _JobEvent:
    kind: str
    payload: Any


class _BatchJob:
    """父进程侧单个 worker 的非阻塞状态机。

    持有管道连接、输入迭代器、进度计数和取消/超时截止时间；poll() 每次驱动
    一步，终态事件通过 _JobEvent 返回给 BatchJobRunner。
    """

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
            # 子进程端连接只应由子进程持有；父进程立即关闭，避免两端同持一份
            # 句柄导致 EOF 判定失真。
            child_connection.close()
        _apply_worker_priority(process)

    @property
    def is_terminal(self) -> bool:
        return self._terminal

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def request_cancel(self) -> None:
        """请求取消当前任务，并记录宽限期截止时间。"""
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
        """单步驱动状态机：检查超时、收消息，返回一个终态/结果事件。"""
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

        if not has_message and not self._process.is_alive():
            return self._handle_worker_exit()
        return None

    def _check_timeout(self, now: float) -> None:
        """超过 deadline 时终止子进程并抛出 JobTimeoutError。"""
        if self._deadline is None or now < self._deadline:
            return
        self._terminate_and_join()
        self._terminal = True
        raise JobTimeoutError(
            f"batch job timed out after {self._timeout:.3f} seconds"
        )

    def _handle_message(self, message: Any) -> _JobEvent | None:
        """按 IPC 消息类型推进状态机，未知/非法消息视为协议错误。"""
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

        if kind == REQUEST_INPUT:
            self._maybe_send_next_batch()
            return None

        if kind == JOB_ERROR:
            self._terminate_and_join()
            self._terminal = True
            raise JobError(str(payload))

        if kind == DONE:
            return self._handle_done(bool(payload))

        self._fail_protocol(f"unknown worker IPC message: {kind!r}")
        return None

    def _maybe_send_next_batch(self) -> None:
        """仅在收到 INIT_OK 或子进程 REQUEST_INPUT 后下发下一批输入。

        不主动在收到结果后继续灌入输入：输入侧和结果侧交替使用管道，避免
        父进程与子进程同时阻塞在 send() 上形成管道死锁。
        """
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
            # 子进程可能已退出；下一次 poll 会把它转换成 WorkerCrashedError。
            pass

    def _send_next_items_batch(self) -> None:
        """从输入迭代器取一批路径发送给子进程；输入耗尽时发送 END_INPUT。"""
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
            # total 若已指定，输入条数必须与 total 完全一致；不一致属于调用方
            # 契约错误，立即终止任务。
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
        """累计一批结果的成功/失败计数，并生成 ResultBatch 事件。"""
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
        """处理正常完成或取消完成消息，并回收子进程。"""
        cancelled = self._cancel_requested or worker_cancelled
        if not cancelled:
            # 未取消时，正常完成必须满足“输入全部提交且结果全部返回”的契约。
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
        """子进程未发终态消息即退出：取消场景视为取消，其余视为崩溃。"""
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
        """完成取消路径；force=True 时不等子进程协作直接终止。"""
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
        """收到终态消息后优雅回收进程；超时再升级为 terminate/kill。"""
        self._close_connection()
        self._process.join(timeout=_DEFAULT_SHUTDOWN_SECONDS)
        if self._process.is_alive():
            self._terminate_and_join()

    def _terminate_and_join(self) -> None:
        """强制回收子进程：先 terminate，超时后 kill。"""
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
        """结束任务：终态后只回收进程，未终态则强制终止。"""
        if self._terminal:
            self._process.join(timeout=_DEFAULT_SHUTDOWN_SECONDS)
            if self._process.is_alive():
                self._terminate_and_join()
            self._close_connection()
            return
        self._terminate_and_join()
        self._terminal = True


class BatchJobRunner:
    """通用子进程流水线任务基类。

    子类实现 build_pipeline() 声明 worker 侧流水线，再通过 iter_results() 或
    run() 同步驱动任务。本类不含 Qt 集成，调用方应在后台线程中调用。
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
        """逐批产出流水线结果；取消时抛出 JobCancelledError。"""

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
                    # 空输入或没有任何结果批次时，也要补发一次最终进度，
                    # 否则调用方进度条会停在 0。
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
        """收集全部结果并返回 JobSummary；取消时返回 cancelled=True 的摘要。"""

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
