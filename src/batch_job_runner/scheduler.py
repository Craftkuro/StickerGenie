"""Subprocess pipeline scheduler.

Runs inside a dedicated worker process. The scheduler thread owns the pipe
(single-threaded poll/recv/send); stage worker threads only touch queues.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
from multiprocessing.connection import Connection
from typing import Any

from .models import GeneralDataWrapper, PipelineSpec, validate_pipeline_spec


logger = logging.getLogger(__name__)

INIT_OK = "INIT_OK"
INIT_ERROR = "INIT_ERROR"
ITEMS = "ITEMS"
END_INPUT = "END_INPUT"
CANCEL = "CANCEL"
RESULT_BATCH = "RESULT_BATCH"
REQUEST_INPUT = "REQUEST_INPUT"
DONE = "DONE"
JOB_ERROR = "JOB_ERROR"

_POLL_INTERVAL_SECONDS = 0.05
_STOP_WAIT_TIMEOUT_SECONDS = 1.0


def format_error(error: BaseException) -> str:
    """Format an exception as ``TypeName: message`` for IPC error strings."""

    detail = str(error).strip()
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


def _make_failure(
    data: Any,
    error: BaseException,
    stage_name: str,
) -> GeneralDataWrapper:
    return GeneralDataWrapper(
        data=data,
        hasException=True,
        error=format_error(error),
        stage_name=stage_name,
    )


def _send(connection: Connection, kind: str, payload: Any = None) -> None:
    connection.send((kind, payload))


class _WorkerState:
    __slots__ = ("lock", "in_flight", "errors")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.in_flight = 0
        self.errors: list[BaseException] = []


def _clear_queue(target: "queue.Queue[Any]") -> None:
    while True:
        try:
            target.get_nowait()
        except queue.Empty:
            return


def _drain(target: "queue.Queue[Any]", limit: int) -> list[GeneralDataWrapper]:
    batch: list[GeneralDataWrapper] = []
    while len(batch) < limit:
        try:
            batch.append(target.get_nowait())
        except queue.Empty:
            break
    return batch


def _run_stage_worker(
    stage,
    input_queue: "queue.Queue[Any]",
    output_queue: "queue.Queue[Any]",
    stop_event: threading.Event,
    state: _WorkerState,
    *,
    is_last: bool,
) -> None:
    """One worker thread executing a single pipeline stage."""

    while not stop_event.is_set():
        try:
            first = input_queue.get(timeout=_POLL_INTERVAL_SECONDS)
        except queue.Empty:
            continue

        wrappers = [first]
        if stage.batch_size > 1:
            while len(wrappers) < stage.batch_size:
                try:
                    wrappers.append(input_queue.get_nowait())
                except queue.Empty:
                    break

        with state.lock:
            state.in_flight += len(wrappers)

        try:
            if stop_event.is_set() and not is_last:
                # Cancellation: intermediate results are dropped.
                continue

            failed = [wrapper for wrapper in wrappers if wrapper.hasException]
            good = [wrapper for wrapper in wrappers if not wrapper.hasException]
            produced: list[GeneralDataWrapper] = []

            if good:
                if stage.batch_size > 1:
                    raw_inputs = [wrapper.data for wrapper in good]
                    try:
                        raw_outputs = stage.func(raw_inputs)
                    except BaseException as error:
                        produced = [
                            _make_failure(wrapper.data, error, stage.name)
                            for wrapper in good
                        ]
                    else:
                        if (
                            not isinstance(raw_outputs, (list, tuple))
                            or len(raw_outputs) != len(good)
                        ):
                            raise RuntimeError(
                                f"stage {stage.name!r} returned "
                                f"{len(raw_outputs) if isinstance(raw_outputs, (list, tuple)) else '?'} "
                                f"results for {len(good)} inputs"
                            )
                        produced = [
                            GeneralDataWrapper(data=output)
                            for output in raw_outputs
                        ]
                else:
                    try:
                        raw_output = stage.func(good[0].data)
                    except BaseException as error:
                        produced = [
                            _make_failure(good[0].data, error, stage.name)
                        ]
                    else:
                        produced = [GeneralDataWrapper(data=raw_output)]

            for wrapper in failed:
                output_queue.put(wrapper)
            for wrapper in produced:
                output_queue.put(wrapper)
        except BaseException as error:
            # Contract violations (for example a batch length mismatch) are
            # job-level failures reported through JOB_ERROR.
            with state.lock:
                state.errors.append(error)
            stop_event.set()
        finally:
            with state.lock:
                state.in_flight -= len(wrappers)


def _finish_cancelled(
    connection: Connection,
    queues: dict[str, "queue.Queue[Any]"],
    tail_queue: "queue.Queue[Any]",
    stop_event: threading.Event,
    state: _WorkerState,
    result_batch_size: int,
) -> None:
    """Stop workers, drop queued items, then flush in-flight tail results."""

    stop_event.set()
    for target in queues.values():
        _clear_queue(target)

    deadline = time.monotonic() + _STOP_WAIT_TIMEOUT_SECONDS
    while True:
        with state.lock:
            in_flight = state.in_flight
        batch = _drain(tail_queue, result_batch_size)
        if batch:
            try:
                _send(connection, RESULT_BATCH, tuple(batch))
            except (EOFError, BrokenPipeError, OSError):
                # The parent may have force-terminated the worker already.
                return
        if in_flight == 0 and tail_queue.empty():
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_INTERVAL_SECONDS)
    try:
        _send(connection, DONE, True)
    except (EOFError, BrokenPipeError, OSError):
        pass


def scheduler_entry(connection: Connection, spec: PipelineSpec) -> None:
    """Top-level spawn target for one batch job worker process."""

    try:
        validate_pipeline_spec(spec)
    except BaseException as error:
        logger.error("batch job spec validation failed: %s", error)
        _send(connection, INIT_ERROR, format_error(error))
        return

    startup_info: Any = None
    if spec.setup_func is not None:
        try:
            startup_info = spec.setup_func()
        except BaseException as error:
            logger.error("batch job worker initialization failed: %s", error)
            _send(connection, INIT_ERROR, format_error(error))
            return

    queues: dict[str, "queue.Queue[Any]"] = {
        queue_spec.name: queue.Queue(maxsize=queue_spec.maxsize)
        for queue_spec in spec.queues
    }
    stop_event = threading.Event()
    state = _WorkerState()
    cancelled = False

    try:
        for index, stage in enumerate(spec.stages):
            is_last = index == len(spec.stages) - 1
            input_queue = queues[stage.input_queue]
            output_queue = queues[stage.output_queue]
            for worker_index in range(stage.pool_size):
                thread = threading.Thread(
                    target=_run_stage_worker,
                    args=(stage, input_queue, output_queue, stop_event, state),
                    kwargs={"is_last": is_last},
                    name=f"batch-stage-{stage.name}-{worker_index}",
                    daemon=True,
                )
                thread.start()

        _send(connection, INIT_OK, startup_info)

        first_queue = queues[spec.stages[0].input_queue]
        tail_queue = queues[spec.stages[-1].output_queue]
        fed = 0
        drained = 0
        input_exhausted = False
        received_items = False
        request_sent = False

        while True:
            if state.errors:
                _send(connection, JOB_ERROR, format_error(state.errors[0]))
                return

            if connection.poll(_POLL_INTERVAL_SECONDS):
                kind, payload = connection.recv()
                if kind == ITEMS:
                    if not isinstance(payload, (tuple, list)):
                        raise RuntimeError("ITEMS requires a tuple payload")
                    received_items = True
                    request_sent = False
                    for item in payload:
                        while True:
                            try:
                                first_queue.put_nowait(
                                    GeneralDataWrapper(data=item)
                                )
                                break
                            except queue.Full:
                                # Never block the pipe loop on a full input
                                # queue: drain the tail queue first so
                                # downstream stages can make progress, then
                                # retry. This prevents a circular wait between
                                # the scheduler, stage workers and the tail.
                                batch = _drain(
                                    tail_queue, spec.result_batch_size
                                )
                                if batch:
                                    _send(
                                        connection,
                                        RESULT_BATCH,
                                        tuple(batch),
                                    )
                                    drained += len(batch)
                                if state.errors:
                                    _send(
                                        connection,
                                        JOB_ERROR,
                                        format_error(state.errors[0]),
                                    )
                                    return
                                time.sleep(0.001)
                        fed += 1
                elif kind == END_INPUT:
                    input_exhausted = True
                    request_sent = False
                elif kind == CANCEL:
                    cancelled = True
                    break
                else:
                    raise RuntimeError(
                        f"unknown parent IPC message: {kind!r}"
                    )

            if not request_sent:
                batch = _drain(tail_queue, spec.result_batch_size)
                if batch:
                    _send(connection, RESULT_BATCH, tuple(batch))
                    drained += len(batch)

                if input_exhausted and fed == drained:
                    _send(connection, DONE, False)
                    return

                if (
                    not input_exhausted
                    and received_items
                    and not batch
                ):
                    _send(connection, REQUEST_INPUT, None)
                    request_sent = True
    except BaseException as error:
        logger.error("batch job worker failed:\n%s", traceback.format_exc())
        try:
            _send(connection, JOB_ERROR, format_error(error))
        except (EOFError, BrokenPipeError, OSError):
            pass
        return
    finally:
        if cancelled:
            _finish_cancelled(
                connection,
                queues,
                tail_queue,
                stop_event,
                state,
                spec.result_batch_size,
            )
