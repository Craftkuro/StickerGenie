"""子进程侧流水线调度器。

本模块运行在独立 worker 进程中：调度线程独占管道（poll/recv/send），
各 stage 的 worker 线程只访问队列。父进程与子进程使用
(kind, payload) 信封通信，并通过 REQUEST_INPUT 握手避免双向大消息
同时阻塞在管道上。
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

# 子进程 IPC 消息类型；父进程 job.py 使用同一组常量。
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
        # 当前仍在 stage worker 手中、尚未写入队列的条数，取消路径依赖它判断
        # 最后一步是否还有在途结果需要送出。
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
    """执行单个流水线步骤的工作线程。

    对每个输入 wrapper：已失败的透传、正常的进入用户函数；用户函数抛异常时
    把整批输入标记为失败，但任务本身继续运行。batch_size > 1 时按批调用。
    """

    while not stop_event.is_set():
        try:
            first = input_queue.get(timeout=_POLL_INTERVAL_SECONDS)
        except queue.Empty:
            continue

        wrappers = [first]
        if stage.batch_size > 1:
            # 先阻塞取一条保证有工作，再非阻塞凑批；流尾不足一批就按实际数量处理。
            while len(wrappers) < stage.batch_size:
                try:
                    wrappers.append(input_queue.get_nowait())
                except queue.Empty:
                    break

        with state.lock:
            state.in_flight += len(wrappers)

        try:
            if stop_event.is_set() and not is_last:
                # 取消后中间步骤的在途结果直接丢弃；最后一步仍会产出结果，
                # 以便父进程收到有界的取消宽限结果。
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
            # 批长度不匹配等契约错误属于 job 级问题，通过 JOB_ERROR 上报。
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
    """停止 worker、丢弃排队数据，并送完最后一步的在途结果。

    等待最多 _STOP_WAIT_TIMEOUT_SECONDS；若 worker 无法及时停止，直接发
    DONE(True) 退出，父进程会通过 terminate/kill 兜底回收进程。
    """

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
    """子进程入口（spawn target）。

    整体过程分为四个阶段：

    1. 启动前校验/初始化：校验 PipelineSpec；若声明 setup_func（加载模型、
       OCR 引擎等），先执行一次。失败通过 INIT_ERROR 上报，父进程转成
       WorkerInitializationError。
    2. 创建流水线：按 spec 创建有界队列，为每个 stage 启动 pool_size 个
       worker 线程；全部就绪后发送 INIT_OK(startup_info)，父进程才开始下发输入。
    3. 调度循环（唯一持有管道的线程）：收父进程消息，把 ITEMS 放入首队列；
       从尾队列按批回传结果；尾队列空且输入未结束时发 REQUEST_INPUT 拉取
       下一批输入；输入耗尽且 fed == drained 时发 DONE(False) 正常结束。
    4. 异常/取消兜底：协议错误或 stage 契约错误发 JOB_ERROR；收到 CANCEL
       后调用 _finish_cancelled 清空队列并送出最后一步在途结果，再发 DONE(True)。
    """

    try:
        # 阶段 1a：校验流水线定义；失败属于 job 级初始化失败，不启动任何线程。
        validate_pipeline_spec(spec)
    except BaseException as error:
        logger.error("batch job spec validation failed: %s", error)
        _send(connection, INIT_ERROR, format_error(error))
        return

    startup_info: Any = None
    if spec.setup_func is not None:
        try:
            # 阶段 1b：加载模型/引擎等进程内单例；返回值随 INIT_OK 传回父进程。
            startup_info = spec.setup_func()
        except BaseException as error:
            logger.error("batch job worker initialization failed: %s", error)
            _send(connection, INIT_ERROR, format_error(error))
            return

    # 阶段 2a：创建有界队列。maxsize 既是内存上限，也为相邻 stage 提供背压。
    queues: dict[str, "queue.Queue[Any]"] = {
        queue_spec.name: queue.Queue(maxsize=queue_spec.maxsize)
        for queue_spec in spec.queues
    }
    stop_event = threading.Event()
    state = _WorkerState()
    cancelled = False

    try:
        # 阶段 2b：为每个 stage 启动 pool_size 个 worker 线程。is_last 标记
        # 最后一个 stage：取消时中间步骤的在途结果会被丢弃，最后一步仍会产出。
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

        # 阶段 2c：所有线程就绪后告知父进程可以开始下发输入；此后单条数据
        # 失败只标记 wrapper，不再影响任务整体。
        _send(connection, INIT_OK, startup_info)

        first_queue = queues[spec.stages[0].input_queue]
        tail_queue = queues[spec.stages[-1].output_queue]
        # 调度循环状态：
        # - fed/drained：已入队输入数 / 已回传结果数；流水线 1:1 输出，两者
        #   相等且输入结束时即可判定任务完成。
        # - input_exhausted：父进程已发 END_INPUT，之后不会再有新输入。
        # - received_items：是否已收到过 ITEMS，避免一启动就无限请求输入。
        # - request_sent：是否已发出 REQUEST_INPUT；为 True 时暂停回传结果。
        fed = 0
        drained = 0
        input_exhausted = False
        received_items = False
        request_sent = False

        # 阶段 3：调度循环是子进程唯一读/写管道的线程；stage worker 只操作
        # 队列。每轮先处理可读消息，再回传尾队列结果，最后决定是否拉新输入。
        while True:
            if state.errors:
                # stage worker 上报了契约级错误（例如批量输出长度不匹配），
                # 这类错误无法逐条恢复，直接终止整个任务。
                _send(connection, JOB_ERROR, format_error(state.errors[0]))
                return

            if connection.poll(_POLL_INTERVAL_SECONDS):
                # 有可读消息时 recv 不会阻塞；未知消息在下面按协议错误处理。
                kind, payload = connection.recv()
                if kind == ITEMS:
                    if not isinstance(payload, (tuple, list)):
                        raise RuntimeError("ITEMS requires a tuple payload")
                    # 收到新输入后解除“暂停回传结果”状态，可继续向父进程发结果；
                    # 随后逐个包装成 GeneralDataWrapper 放入首队列。
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
                                # 首队列满时不能阻塞调度线程：先 drain 尾队列
                                # 把结果发给父进程，让下游继续推进，再重试入队；
                                # 否则调度线程、stage worker 和尾队列会互相等待。
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
                    # 父进程已提交全部输入；之后不再发 ITEMS，等尾队列清空即完成。
                    input_exhausted = True
                    request_sent = False
                elif kind == CANCEL:
                    # 收到取消信号：跳出调度循环，进入阶段 4 的取消收尾。
                    cancelled = True
                    break
                else:
                    raise RuntimeError(
                        f"unknown parent IPC message: {kind!r}"
                    )

            # 结果回传阶段：request_sent 为 True 时表示已向父进程请求下一批
            # 输入，此时暂停发送 RESULT_BATCH，直到收到 ITEMS/END_INPUT。
            # 这个单向交替协议保证管道中不会同时出现双向的大消息，避免父子
            # 进程互相阻塞在 send() 上形成死锁。
            if not request_sent:
                batch = _drain(tail_queue, spec.result_batch_size)
                if batch:
                    _send(connection, RESULT_BATCH, tuple(batch))
                    drained += len(batch)

                if input_exhausted and fed == drained:
                    # 输入已全部提交，且每条输入都产生了结果并回传完毕：正常完成。
                    _send(connection, DONE, False)
                    return

                if (
                    not input_exhausted
                    and received_items
                    and not batch
                ):
                    # 输入未结束、尾队列也已空，说明流水线需要更多输入才能
                    # 继续推进：向父进程拉取下一批，并进入暂停回传状态。
                    _send(connection, REQUEST_INPUT, None)
                    request_sent = True
    except BaseException as error:
        # 调度循环内未捕获的异常统一按 job 级失败处理，把错误串发给父进程。
        logger.error("batch job worker failed:\n%s", traceback.format_exc())
        try:
            _send(connection, JOB_ERROR, format_error(error))
        except (EOFError, BrokenPipeError, OSError):
            pass
        return
    finally:
        if cancelled:
            # 阶段 4：停止 worker、清空队列，并送出最后一步在途结果后结束。
            _finish_cancelled(
                connection,
                queues,
                tail_queue,
                stop_event,
                state,
                spec.result_batch_size,
            )
