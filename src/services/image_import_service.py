"""图片导入的 Qt 后台服务。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from services import import_images
from commons.signal_objects import ImportImagesRequest
from services.background_job import BackgroundJobService


class ImageImportService(BackgroundJobService):
    """在独立 QThread 中执行导入并通过导入专用信号回传结果。"""

    import_finished = pyqtSignal(object)
    import_cancelled = pyqtSignal(object)
    import_failed = pyqtSignal(str)
    import_progress_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.succeeded.connect(self.import_finished)
        self.cancelled.connect(self.import_cancelled)
        self.failed.connect(self.import_failed)
        self.progress_changed.connect(self.import_progress_changed)

    def start_import(self, request: ImportImagesRequest) -> None:
        if not isinstance(request, ImportImagesRequest):
            raise TypeError("request must be an ImportImagesRequest")
        if self.active_job_count:
            raise RuntimeError("已有图片导入任务正在进行")

        file_paths = list(request.file_paths)

        def run(progress, cancel_event):
            return import_images.import_images_with_result(
                file_paths,
                generate_vectors=request.generate_vectors,
                extract_text=request.extract_text,
                progress=progress,
                cancel_event=cancel_event,
            )

        self.start(run, cancel_allowed=None)

    def cancel_import(self) -> bool:
        return self.cancel()
