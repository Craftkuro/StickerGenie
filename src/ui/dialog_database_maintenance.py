# coding=utf-8
from PyQt6 import uic
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import QDialog

import apppath
from services.database_maintenance import (
    DatabaseMaintenanceOptions,
    DatabaseMaintenanceProgress,
    VectorMaintenanceScope,
)


class DatabaseMaintenanceDialog(QDialog):
    maintenance_requested = pyqtSignal(object)
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._cancel_requested = False

        ui_file_path = apppath.app_path / "ui" / "dialog_database_maintenance.ui"
        uic.loadUi(ui_file_path, self)

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.checkBoxExtractText.toggled.connect(self._update_controls)
        self.checkBoxGenerateVectors.toggled.connect(self._update_controls)
        self.checkBoxDeleteOrphanBlobs.toggled.connect(self._update_controls)
        self.checkBoxDeleteThumbnailCache.toggled.connect(self._update_controls)
        self.pushButtonStart.clicked.connect(self._request_start)
        self.pushButtonCancel.clicked.connect(self._cancel_or_close)
        self._update_controls()

    def selected_options(self) -> DatabaseMaintenanceOptions:
        scope = (
            VectorMaintenanceScope.MISSING
            if self.comboBoxVectorScope.currentIndex() == 0
            else VectorMaintenanceScope.ALL
        )
        return DatabaseMaintenanceOptions(
            delete_orphan_blobs=self.checkBoxDeleteOrphanBlobs.isChecked(),
            extract_text=self.checkBoxExtractText.isChecked(),
            generate_vectors=self.checkBoxGenerateVectors.isChecked(),
            vector_scope=scope,
            delete_thumbnail_cache=self.checkBoxDeleteThumbnailCache.isChecked(),
        )

    def update_progress(self, progress: DatabaseMaintenanceProgress) -> None:
        if not isinstance(progress, DatabaseMaintenanceProgress):
            raise TypeError("progress must be a DatabaseMaintenanceProgress")

        self.progressBar.setValue(progress.percent)
        if self._cancel_requested:
            self.labelStatus.setText("正在中止当前任务")
        else:
            self.labelStatus.setText(progress.status)

        if progress.total:
            self.labelTaskProgress.setText(
                f"{progress.task_name}：{progress.completed}/{progress.total}"
            )
        else:
            self.labelTaskProgress.setText(progress.task_name)

        can_cancel = progress.cancellable and not self._cancel_requested
        self.pushButtonCancel.setText("中止" if progress.cancellable else "处理中")
        self.pushButtonCancel.setEnabled(can_cancel)

    def finish(self) -> None:
        self._running = False
        self.close()

    def _update_controls(self) -> None:
        if self._running:
            return
        self.comboBoxVectorScope.setEnabled(
            self.checkBoxGenerateVectors.isChecked()
        )
        self.pushButtonStart.setEnabled(
            self.checkBoxDeleteOrphanBlobs.isChecked()
            or self.checkBoxExtractText.isChecked()
            or self.checkBoxGenerateVectors.isChecked()
            or self.checkBoxDeleteThumbnailCache.isChecked()
        )

    def _request_start(self) -> None:
        if self._running:
            return

        options = self.selected_options()
        self._running = True
        self._cancel_requested = False
        self.groupBoxOperations.setEnabled(False)
        self.pushButtonStart.setEnabled(False)
        self.pushButtonCancel.setText("处理中")
        self.pushButtonCancel.setEnabled(False)
        self.labelStatus.setText("正在准备数据库维护")
        self.labelTaskProgress.setText("")
        self.progressBar.setValue(0)
        self.maintenance_requested.emit(options)

    def _cancel_or_close(self) -> None:
        if not self._running:
            self.reject()
            return
        if self._cancel_requested or not self.pushButtonCancel.isEnabled():
            return

        self._cancel_requested = True
        self.pushButtonCancel.setEnabled(False)
        self.labelStatus.setText("正在中止当前任务")
        self.cancel_requested.emit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._running:
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if not self._running:
            super().reject()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._running:
            event.ignore()
            return
        super().keyPressEvent(event)
