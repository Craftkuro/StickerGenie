# coding=utf-8
from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QKeyEvent, QResizeEvent
from PyQt6.QtWidgets import QDialog

import apppath
from services.import_images import ImportImagesProgress


class ImageImportProgressDialog(QDialog):
    """展示单次图片导入任务的状态与进度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_close = False
        self._last_file_name: str | None = None
        self._detail_placeholder = "正在检查文件和重复项"

        ui_file_path = apppath.app_path / "ui" / "dialog_image_import_progress.ui"
        uic.loadUi(ui_file_path, self)

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

    def update_progress(self, progress: ImportImagesProgress) -> None:
        if not isinstance(progress, ImportImagesProgress):
            raise TypeError("progress must be an ImportImagesProgress")

        status = progress.status
        self.labelStatus.setText(status)
        self.progressBar.setValue(progress.percent)
        self._last_file_name = progress.last_file_name
        if progress.status == "正在预处理":
            self._detail_placeholder = "正在检查文件和重复项"
        elif progress.status == "正在导入图片":
            self._detail_placeholder = "正在处理图片数据"
        else:
            self._detail_placeholder = ""
        self._render_detail()

    def finish(self) -> None:
        self._can_close = True
        self.close()

    def _render_detail(self) -> None:
        if self._last_file_name:
            full_text = f"最后完成：{self._last_file_name}"
            text = self.labelDetail.fontMetrics().elidedText(
                full_text,
                Qt.TextElideMode.ElideMiddle,
                max(0, self.labelDetail.width()),
            )
            self.labelDetail.setText(text)
            self.labelDetail.setToolTip(full_text)
            return

        self.labelDetail.setText(self._detail_placeholder)
        self.labelDetail.setToolTip("")

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

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render_detail()
