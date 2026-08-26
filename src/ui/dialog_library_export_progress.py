"""图库导出进度对话框。

导出完成前禁止手动关闭，避免后台任务仍在写入导出目录时离开进度界面。
"""

from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import QDialog

import apppath
from services.export_library import ExportLibraryProgress


class LibraryExportProgressDialog(QDialog):
    """展示单次图库导出的状态与进度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_close = False

        ui_file_path = apppath.app_path / "ui" / "dialog_library_export_progress.ui"
        uic.loadUi(ui_file_path, self)

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

    def update_progress(self, progress: ExportLibraryProgress) -> None:
        """用最新进度刷新状态和进度条。"""
        if not isinstance(progress, ExportLibraryProgress):
            raise TypeError("progress must be an ExportLibraryProgress")

        self.labelStatus.setText(progress.status)
        self.progressBar.setValue(progress.percent)
        if progress.total:
            self.labelTaskProgress.setText(
                f"已处理 {progress.completed}/{progress.total}"
            )
        else:
            self.labelTaskProgress.setText("")

    def finish(self) -> None:
        """允许控制器在任务终态时关闭对话框。"""
        self._can_close = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._can_close:
            super().closeEvent(event)
        else:
            event.ignore()

    def reject(self) -> None:
        if self._can_close:
            super().reject()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and not self._can_close:
            event.ignore()
            return
        super().keyPressEvent(event)
