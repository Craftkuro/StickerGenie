"""通用 Qt 后台任务服务。

业务模块只提供同步函数；需要放到 QThread 中运行并通过信号回传进度和终态时，
统一交给 BackgroundJobService。每个服务实例同一时间只允许一个活动任务。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from functools import partial
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot


logger = logging.getLogger(__name__)

ProgressCallback = Callable[[Any], None]
JobFunction = Callable[[ProgressCallback | None, threading.Event | None], Any]


class _BackgroundJobWorker(QObject):
    """在独立 QThread 中执行同步业务函数并通过信号回传结果。"""

    succeeded = pyqtSignal(object)
    cancelled = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress_changed = pyqtSignal(object)

    def __init__(
        self,
        job: JobFunction,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self._job = job
        self._cancel_event = cancel_event

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self._job(
                self.progress_changed.emit,
                self._cancel_event,
            )
        except Exception as exc:
            logger.exception("后台任务失败")
            self.failed.emit(str(exc))
            return

        if bool(getattr(result, "cancelled", False)):
            self.cancelled.emit(result)
        else:
            self.succeeded.emit(result)


class BackgroundJobService(QObject):
    """管理后台任务线程、进度转发与协作式取消。"""

    succeeded = pyqtSignal(object)
    cancelled = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: dict[QThread, _BackgroundJobWorker] = {}
        self._cancel_events: dict[QThread, threading.Event] = {}
        self._cancel_predicates: dict[
            QThread, Callable[[Any], bool] | None
        ] = {}
        self._can_cancel = False

    @property
    def active_job_count(self) -> int:
        return len(self._jobs)

    def start(
        self,
        job: JobFunction,
        *,
        cancel_allowed: Callable[[Any], bool] | None = None,
    ) -> None:
        """启动一个后台任务。

        cancel_allowed 为 None 时任务启动后即可取消；传入回调时，只有回调
        根据最新进度返回 True 的阶段才允许取消。
        """
        if self._jobs:
            raise RuntimeError("已有后台任务正在进行")

        thread = QThread(self)
        cancel_event = threading.Event()
        worker = _BackgroundJobWorker(job, cancel_event)
        worker.moveToThread(thread)
        self._jobs[thread] = worker
        self._cancel_events[thread] = cancel_event
        self._cancel_predicates[thread] = cancel_allowed
        self._can_cancel = cancel_allowed is None

        thread.started.connect(worker.run)
        worker.succeeded.connect(self.succeeded)
        worker.cancelled.connect(self.cancelled)
        worker.failed.connect(self.failed)
        worker.progress_changed.connect(self._forward_progress)
        worker.succeeded.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.succeeded.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(partial(self._release_job, thread))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    @pyqtSlot(object)
    def _forward_progress(self, progress: Any) -> None:
        predicate = next(iter(self._cancel_predicates.values()), None)
        if predicate is not None:
            try:
                self._can_cancel = bool(predicate(progress))
            except Exception:
                logger.exception("计算后台任务可取消状态失败")
                self._can_cancel = False
        self.progress_changed.emit(progress)

    def cancel(self) -> bool:
        if not self._can_cancel or not self._cancel_events:
            return False

        cancel_event = next(iter(self._cancel_events.values()))
        if cancel_event.is_set():
            return False
        cancel_event.set()
        return True

    def _release_job(self, thread: QThread) -> None:
        self._jobs.pop(thread, None)
        self._cancel_events.pop(thread, None)
        self._cancel_predicates.pop(thread, None)
        self._can_cancel = False
