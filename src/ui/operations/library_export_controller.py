"""图库导出的界面控制器。"""

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from services.export_library import LibraryExportService

from ..dialog_library_export_progress import LibraryExportProgressDialog


class LibraryExportController:
    """负责图库导出全流程：目录选择、进度与终态处理。"""

    def __init__(self, window, service: LibraryExportService):
        self._window = window
        self._service = service
        self._dialog = None
        service.export_finished.connect(self._on_export_library_finished)
        service.export_failed.connect(self._on_export_library_failed)
        service.export_progress_changed.connect(
            self._on_export_library_progress_changed
        )

    def export_library(self):
        destination = QFileDialog.getExistingDirectory(
            self._window,
            "选择导出目录",
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DesktopLocation
            ),
        )
        if not destination:
            return

        self._window.actionExportLibrary.setEnabled(False)
        dialog = LibraryExportProgressDialog(self._window)
        self._dialog = dialog
        dialog.open()
        try:
            self._service.start_export(destination)
        except Exception as exc:
            self._on_export_library_failed(str(exc))

    def _on_export_library_progress_changed(self, progress):
        dialog = self._dialog
        if dialog is not None:
            dialog.update_progress(progress)

    def _close_export_library_progress_dialog(self):
        dialog = self._dialog
        self._dialog = None
        if dialog is not None:
            dialog.finish()
            dialog.deleteLater()

    def _on_export_library_finished(self, result):
        self._close_export_library_progress_dialog()
        self._window.actionExportLibrary.setEnabled(True)
        QMessageBox.information(
            self._window,
            "导出完成",
            f"导出完成，已导出{result.image_count}个图片和{result.tag_count}个标签。",
        )

    def _on_export_library_failed(self, error_message: str):
        self._close_export_library_progress_dialog()
        self._window.actionExportLibrary.setEnabled(True)
        QMessageBox.critical(self._window, "导出失败", error_message)
