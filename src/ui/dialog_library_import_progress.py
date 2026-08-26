# coding=utf-8
"""图库备份导入进度对话框。

导入结束前禁止手动关闭，只允许在逐张处理图片的阶段请求中止。
"""
from PyQt6 import uic
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import QDialog

import apppath
from services.import_library import LibraryImportProgress


class LibraryImportProgressDialog(QDialog):
    """展示单次图库备份导入任务的状态与进度。"""

    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_close = False
        self._cancel_requested = False

        ui_file_path = apppath.app_path / "ui" / "dialog_library_import_progress.ui"
        uic.loadUi(ui_file_path, self)

        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.pushButtonCancel.setEnabled(False)
        self.pushButtonCancel.clicked.connect(self._request_cancel)

    def update_progress(self, progress: LibraryImportProgress) -> None:
        """用最新进度刷新状态、进度条、处理数量与中止按钮。"""
        if not isinstance(progress, LibraryImportProgress):
            raise TypeError("progress must be a LibraryImportProgress")

        self.progressBar.setValue(progress.percent)
        self.labelStatus.setText(
            "正在中止" if self._cancel_requested else progress.status
        )
        if progress.total:
            self.labelTaskProgress.setText(
                f"已处理 {progress.completed}/{progress.total}"
            )
        else:
            self.labelTaskProgress.setText("")
        self.pushButtonCancel.setEnabled(
            progress.cancellable and not self._cancel_requested
        )

    def _request_cancel(self) -> None:
        """请求中止导入：禁用按钮并发出信号，由服务层置位 cancel_event。"""
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self.pushButtonCancel.setEnabled(False)
        self.labelStatus.setText("正在中止")
        self.cancel_requested.emit()

    def finish(self) -> None:
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
