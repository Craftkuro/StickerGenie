"""数据库维护的界面控制器。"""

from PyQt6.QtWidgets import QMessageBox

import services.global_instances
from services.database_maintenance_service import DatabaseMaintenanceService

from ..dialog_database_maintenance import DatabaseMaintenanceDialog


class DatabaseMaintenanceController:
    """负责数据库维护全流程：对话框生命周期、进度与终态处理。"""

    def __init__(self, window, service: DatabaseMaintenanceService):
        self._window = window
        self._service = service
        self._dialog = None
        service.maintenance_finished.connect(
            self._on_database_maintenance_finished
        )
        service.maintenance_cancelled.connect(
            self._on_database_maintenance_cancelled
        )
        service.maintenance_failed.connect(
            self._on_database_maintenance_failed
        )
        service.maintenance_progress_changed.connect(
            self._on_database_maintenance_progress_changed
        )

    def open_database_maintenance(self):
        if services.global_instances.current_library_db is None:
            QMessageBox.warning(self._window, "无法打开", "仓库数据库尚未初始化。")
            return
        if services.global_instances.current_blob_storage is None:
            QMessageBox.warning(self._window, "无法打开", "Blob存储尚未初始化。")
            return

        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return

        dialog = DatabaseMaintenanceDialog(self._window)
        self._dialog = dialog
        dialog.maintenance_requested.connect(self.start_database_maintenance)
        dialog.cancel_requested.connect(
            self._service.cancel_maintenance
        )
        dialog.finished.connect(
            lambda _result, current=dialog: self._release_database_maintenance_dialog(
                current
            )
        )
        dialog.open()

    def start_database_maintenance(self, options):
        self._window.actionStartDatabaseMaintenance.setEnabled(False)
        self._window.statusBar().showMessage("正在进行数据库维护…")
        try:
            self._service.start_maintenance(options)
        except Exception as exc:
            self._on_database_maintenance_failed(str(exc))

    def _on_database_maintenance_progress_changed(self, progress):
        dialog = self._dialog
        if dialog is not None:
            dialog.update_progress(progress)

        message = progress.status
        if progress.total:
            message += f"（{progress.completed}/{progress.total}）"
        self._window.statusBar().showMessage(message)

    def _close_database_maintenance_dialog(self):
        dialog = self._dialog
        self._dialog = None
        if dialog is not None:
            dialog.finish()
            dialog.deleteLater()

    def _release_database_maintenance_dialog(self, dialog):
        if self._dialog is dialog:
            self._dialog = None
        dialog.deleteLater()

    @staticmethod
    def _database_maintenance_summary(result) -> str:
        parts = [f"已删除 {result.deleted_blob_count} 个未引用Blob"]
        parts.append(f"识别 {result.ocr_count} 张图片文字")
        if result.deleted_thumbnail_count:
            parts.append(
                f"删除 {result.deleted_thumbnail_count} 个缩略图缓存"
            )
        parts.append(f"生成 {result.vectorized_count} 个向量")
        parts.append(f"修复 {result.relinked_vector_count} 个向量关联")
        return "，".join(parts) + "。"

    def _on_database_maintenance_finished(self, result):
        self._window.actionStartDatabaseMaintenance.setEnabled(True)
        self._close_database_maintenance_dialog()
        message = self._database_maintenance_summary(result)
        self._window.statusBar().showMessage(message, 8000)
        QMessageBox.information(self._window, "数据库维护完成", message)

        errors = (
            result.blob_errors
            + result.ocr_errors
            + result.vector_errors
            + result.thumbnail_errors
        )
        if errors:
            details = "\n".join(errors[:10])
            remaining = len(errors) - 10
            if remaining > 0:
                details += f"\n另有 {remaining} 项未显示。"
            QMessageBox.warning(self._window, "部分维护操作失败", details)

    def _on_database_maintenance_cancelled(self, result):
        self._window.actionStartDatabaseMaintenance.setEnabled(True)
        self._close_database_maintenance_dialog()
        message = "数据库维护已中止。" + self._database_maintenance_summary(result)
        self._window.statusBar().showMessage(message, 8000)
        QMessageBox.information(self._window, "数据库维护已中止", message)

    def _on_database_maintenance_failed(self, error_message: str):
        self._window.actionStartDatabaseMaintenance.setEnabled(True)
        self._close_database_maintenance_dialog()
        self._window.statusBar().clearMessage()
        QMessageBox.critical(self._window, "数据库维护失败", error_message)