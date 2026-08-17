"""图库导出的界面控制器。"""

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from services.export_library import LibraryExportService


class LibraryExportController:
    """负责图库导出全流程：目录选择、进度与终态处理。"""

    def __init__(self, window, service: LibraryExportService):
        self._window = window
        self._service = service
        service.export_finished.connect(self._on_export_library_finished)
        service.export_failed.connect(self._on_export_library_failed)
        service.export_progress_changed.connect(
            self._on_export_library_progress_changed
        )

    def export_library(self):
        destination = QFileDialog.getExistingDirectory(
            self._window,
            "选择导出目录",
            "",
        )
        if not destination:
            return

        self._window.actionExportLibrary.setEnabled(False)
        self._window.statusBar().showMessage("正在导出图库…")
        try:
            self._service.start_export(destination)
        except Exception as exc:
            self._window.actionExportLibrary.setEnabled(True)
            self._window.statusBar().clearMessage()
            QMessageBox.critical(self._window, "导出失败", str(exc))

    def _on_export_library_progress_changed(self, progress):
        message = progress.status
        if progress.total:
            message += f"（{progress.completed}/{progress.total}）"
        self._window.statusBar().showMessage(message)

    def _on_export_library_finished(self, result):
        self._window.actionExportLibrary.setEnabled(True)
        self._window.statusBar().showMessage(
            f"已导出 {result.image_count} 个图片和 {result.tag_count} 个标签",
            8000,
        )
        QMessageBox.information(
            self._window,
            "导出完成",
            f"导出完成，已导出{result.image_count}个图片和{result.tag_count}个标签。",
        )

    def _on_export_library_failed(self, error_message: str):
        self._window.actionExportLibrary.setEnabled(True)
        self._window.statusBar().clearMessage()
        QMessageBox.critical(self._window, "导出失败", error_message)