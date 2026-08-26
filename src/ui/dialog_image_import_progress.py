# coding=utf-8
"""图片导入进度对话框。

导入完成前禁止关闭/按 Esc 退出，只允许通过取消按钮请求中止，避免用户
在后台任务仍写入数据库时提前关闭界面。
"""
from PyQt6 import uic
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import QDialog

import apppath
from services.import_images import ImportImagesProgress


class ImageImportProgressDialog(QDialog):
    """展示单次图片导入任务的状态与进度。"""

    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_close = False
        self._cancel_requested = False

        ui_file_path = apppath.app_path / "ui" / "dialog_image_import_progress.ui"
        uic.loadUi(ui_file_path, self)

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.pushButtonCancel.clicked.connect(self._request_cancel)

    def update_progress(self, progress: ImportImagesProgress) -> None:
        """用最新进度刷新状态、进度条和处理数量。"""
        if not isinstance(progress, ImportImagesProgress):
            raise TypeError("progress must be an ImportImagesProgress")

        status = "正在中止" if self._cancel_requested else progress.status
        self.labelStatus.setText(status)
        self.progressBar.setValue(progress.percent)
        if progress.total:
            self.labelTaskProgress.setText(
                f"已处理 {progress.completed}/{progress.total}"
            )
        else:
            self.labelTaskProgress.setText("")

    def _request_cancel(self) -> None:
        """请求中止导入：禁用取消按钮并发出信号，由服务层置位 cancel_event。"""
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
