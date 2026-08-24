"""图片导入的界面控制器。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

import services.sticker_library_viewer_service
from commons.signal_objects import ImportImagesRequest
from services.image_import_service import ImageImportService

from ..dialog_image_import import ImageImportDialog
from ..dialog_image_import_progress import ImageImportProgressDialog
from .taskbar_progress import TaskbarProgressBridge


class ImageImportController:
    """负责图片导入全流程：对话框生命周期、进度与终态处理。"""

    def __init__(self, window, service: ImageImportService, taskbar_progress=None):
        self._window = window
        self._service = service
        self._dialog = None
        self._taskbar = taskbar_progress or TaskbarProgressBridge(window)
        service.import_finished.connect(self._on_import_images_finished)
        service.import_cancelled.connect(self._on_import_images_cancelled)
        service.import_failed.connect(self._on_import_images_failed)
        service.import_progress_changed.connect(
            self._on_import_images_progress_changed
        )

    def basic_import_files(self):
        dialog = ImageImportDialog(self._window)
        dialog.signal_import_requested.connect(
            self.handle_import_images_request,
            type=Qt.ConnectionType.QueuedConnection,
        )
        dialog.exec()

    def handle_import_images_request(self, request: ImportImagesRequest):
        progress_dialog = ImageImportProgressDialog(self._window)
        self._dialog = progress_dialog
        progress_dialog.cancel_requested.connect(
            self._service.cancel_import
        )
        progress_dialog.open()
        try:
            self._service.start_import(request)
        except Exception as exc:
            self._on_import_images_failed(str(exc))
            return
        self._taskbar.begin()
        self._window.statusBar().showMessage("正在导入图片…")

    def _on_import_images_progress_changed(self, progress):
        dialog = self._dialog
        if dialog is not None:
            dialog.update_progress(progress)
        self._taskbar.update(progress.percent)

    def _close_image_import_progress_dialog(self):
        dialog = self._dialog
        self._dialog = None
        if dialog is not None:
            dialog.finish()
            dialog.deleteLater()
        self._taskbar.clear()

    def _on_import_images_finished(self, result):
        self._close_image_import_progress_dialog()
        imported_count = len(result.imported_stickers)
        if imported_count:
            services.sticker_library_viewer_service.wiring.slot_refresh_content()

        message = f"已导入 {imported_count} 张图片"
        if result.vectorized_count:
            message += f"，生成 {result.vectorized_count} 个向量"
        if result.ocr_count:
            message += f"，识别 {result.ocr_count} 张图片文字"
        self._window.statusBar().showMessage(message, 8000)

        detail_parts = [f"已导入 {imported_count} 张图片"]
        if result.vectorized_count:
            detail_parts.append(f"生成 {result.vectorized_count} 个向量")
        if result.ocr_count:
            detail_parts.append(f"识别 {result.ocr_count} 张图片文字")
        if result.duplicate_count:
            detail_parts.append(
                f"另有 {result.duplicate_count} 个重复图片未导入"
            )
        QMessageBox.information(
            self._window,
            "导入完成",
            "，".join(detail_parts) + "。",
        )

        errors = result.vector_errors + result.ocr_errors
        if errors:
            details = "\n".join(errors[:10])
            remaining = len(errors) - 10
            if remaining > 0:
                details += f"\n另有 {remaining} 项未显示。"
            QMessageBox.warning(
                self._window,
                "部分图片处理失败",
                details,
            )

    def _on_import_images_cancelled(self, result):
        self._close_image_import_progress_dialog()
        imported_count = len(result.imported_stickers)
        if imported_count:
            services.sticker_library_viewer_service.wiring.slot_refresh_content()

        message = "导入已中止"
        if imported_count:
            message += f"，已导入 {imported_count} 张图片"
        if result.vectorized_count:
            message += f"，已生成 {result.vectorized_count} 个向量"
        if result.ocr_count:
            message += f"，已识别 {result.ocr_count} 张图片文字"
        if not imported_count and not result.vectorized_count and not result.ocr_count:
            message += "，未导入图片"
        message += "。"
        self._window.statusBar().showMessage(message, 8000)
        QMessageBox.information(self._window, "导入已中止", message)

    def _on_import_images_failed(self, error_message: str):
        self._close_image_import_progress_dialog()
        self._window.statusBar().clearMessage()
        QMessageBox.critical(self._window, "导入失败", error_message)