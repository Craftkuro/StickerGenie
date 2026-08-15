"""数据库维护的 Qt 后台服务。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from services import database_maintenance
from services.background_job import BackgroundJobService
from services.database_maintenance import DatabaseMaintenanceOptions


class DatabaseMaintenanceService(BackgroundJobService):
    """在独立 QThread 中执行维护并通过维护专用信号回传结果。"""

    maintenance_finished = pyqtSignal(object)
    maintenance_cancelled = pyqtSignal(object)
    maintenance_failed = pyqtSignal(str)
    maintenance_progress_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.succeeded.connect(self.maintenance_finished)
        self.cancelled.connect(self.maintenance_cancelled)
        self.failed.connect(self.maintenance_failed)
        self.progress_changed.connect(self.maintenance_progress_changed)

    def start_maintenance(
        self,
        options: DatabaseMaintenanceOptions,
    ) -> None:
        if not isinstance(options, DatabaseMaintenanceOptions):
            raise TypeError("options must be a DatabaseMaintenanceOptions")
        if self.active_job_count:
            raise RuntimeError("已有数据库维护任务正在运行")

        def run(progress, cancel_event):
            return database_maintenance.run_database_maintenance(
                options,
                progress=progress,
                cancel_event=cancel_event,
            )

        self.start(
            run,
            cancel_allowed=lambda progress: bool(
                getattr(progress, "cancellable", False)
            ),
        )

    def cancel_maintenance(self) -> bool:
        return self.cancel()
