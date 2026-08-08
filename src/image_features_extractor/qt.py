"""Qt Signal/Slot adapter driven by a main-thread QTimer."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .extractor import _ExtractionJob
from .models import ExtractionRequest, FeatureResultBatch


logger = logging.getLogger(__name__)


class QtImageFeaturesExtractor(QObject):
    """Run one extraction job without blocking the Qt event loop."""

    started = pyqtSignal(object)
    progress_changed = pyqtSignal(object)
    batch_ready = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent: QObject | None = None, *, poll_interval_ms: int = 20):
        super().__init__(parent)
        if poll_interval_ms <= 0:
            raise ValueError("poll_interval_ms must be greater than zero")

        self._pending_request: ExtractionRequest | None = None
        self._job: _ExtractionJob | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll_job)

    @property
    def is_running(self) -> bool:
        return self._pending_request is not None or self._job is not None

    @pyqtSlot(object)
    def start(self, request: ExtractionRequest) -> None:
        """Schedule a job start and return before model initialization begins."""

        if self.is_running:
            logger.warning("ignored start request while an extraction job is running")
            return
        if not isinstance(request, ExtractionRequest):
            self.failed.emit("start() requires an ExtractionRequest")
            return

        self._pending_request = request
        QTimer.singleShot(0, self._begin_pending_job)

    @pyqtSlot()
    def cancel(self) -> None:
        if self._pending_request is not None:
            self._pending_request = None
            self.cancelled.emit()
            return
        if self._job is not None:
            self._job.request_cancel()

    @pyqtSlot()
    def _begin_pending_job(self) -> None:
        request = self._pending_request
        if request is None:
            return
        self._pending_request = None

        try:
            self._job = _ExtractionJob(
                request.image_paths,
                model_path=request.model_path,
                batch_size=request.batch_size,
                total=request.total,
                timeout=request.timeout,
                providers=request.providers,
                cancel_grace_seconds=request.cancel_grace_seconds,
            )
        except BaseException as error:
            self._job = None
            self.failed.emit(str(error))
            return

        self._timer.start()
        self._poll_job()

    @pyqtSlot()
    def _poll_job(self) -> None:
        job = self._job
        if job is None:
            self._timer.stop()
            return

        try:
            for _ in range(32):
                event = job.poll(0.0)
                if event is None:
                    break
                if event.kind == "started":
                    self.started.emit(event.payload)
                elif event.kind == "batch":
                    batch: FeatureResultBatch = event.payload
                    self.progress_changed.emit(batch.progress)
                    self.batch_ready.emit(batch)
                elif event.kind == "finished":
                    self._release_job()
                    self.finished.emit(event.payload)
                    return
                elif event.kind == "cancelled":
                    self._release_job()
                    self.cancelled.emit()
                    return
                else:
                    raise RuntimeError(f"unknown extraction job event: {event.kind!r}")
        except BaseException as error:
            self._release_job()
            self.failed.emit(str(error))

    def _release_job(self) -> None:
        self._timer.stop()
        job, self._job = self._job, None
        if job is not None:
            job.close()

    def close(self) -> None:
        """Synchronously release a running worker during adapter teardown."""

        self._pending_request = None
        self._release_job()
