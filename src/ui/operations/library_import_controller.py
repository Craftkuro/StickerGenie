"""图库备份导入的界面控制器。"""

from PyQt6.QtWidgets import QFileDialog, QMessageBox

import services.import_library
import services.sticker_library_viewer_service
from services.import_library import LibraryImportService

from ..dialog_library_import_progress import LibraryImportProgressDialog

LIBRARY_IMPORT_CONFIRM_TEXT = (
    "所选图库备份将和当前图库合并，现存的同名标签不会被修改。"
    "如果希望完全覆盖当前图库，请先退出本程序并删除当前图库，"
    "再启动本程序并重试导入。"
)


class LibraryImportController:
    """负责图库备份导入全流程：文件选择、预检、确认、进度与终态处理。"""

    def __init__(self, window, service: LibraryImportService):
        self._window = window
        self._service = service
        self._dialog = None
        self._cancelling = False
        service.import_finished.connect(self._on_import_library_finished)
        service.import_cancelled.connect(self._on_import_library_cancelled)
        service.import_failed.connect(self._on_import_library_failed)
        service.import_progress_changed.connect(
            self._on_import_library_progress_changed
        )

    def import_library_backup(self):
        metadata_path, _ = QFileDialog.getOpenFileName(
            self._window,
            "选择图库备份文件",
            "",
            "metadata.json (metadata.json)",
        )
        if not metadata_path:
            return

        try:
            services.import_library.preflight(metadata_path)
        except services.import_library.LibraryImportError as exc:
            QMessageBox.critical(self._window, "导入失败", str(exc))
            return

        if not self._confirm_library_import(metadata_path):
            return

        self._window.set_write_actions_enabled(False)
        self._cancelling = False
        dialog = LibraryImportProgressDialog(self._window)
        self._dialog = dialog
        dialog.cancel_requested.connect(
            self._on_import_cancel_requested
        )
        dialog.open()
        self._window.statusBar().showMessage("正在导入图库备份…")
        try:
            self._service.start_import(metadata_path)
        except Exception as exc:
            self._on_import_library_failed(str(exc))

    def _confirm_library_import(self, metadata_path: str) -> bool:
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("导入图库备份")
        box.setText(f"已选择备份文件：\n{metadata_path}")
        box.setInformativeText(LIBRARY_IMPORT_CONFIRM_TEXT)
        yes_button = box.addButton(QMessageBox.StandardButton.Yes)
        no_button = box.addButton(QMessageBox.StandardButton.No)
        box.setDefaultButton(no_button)
        box.exec()
        return box.clickedButton() is yes_button

    def _on_import_library_progress_changed(self, progress):
        dialog = self._dialog
        if dialog is not None:
            dialog.update_progress(progress)

        message = (
            "正在中止导入"
            if self._cancelling
            else progress.status
        )
        if progress.total:
            message += f"（{progress.completed}/{progress.total}）"
        self._window.statusBar().showMessage(message)

    def _on_import_cancel_requested(self):
        self._cancelling = True
        self._service.cancel_import()
        self._window.statusBar().showMessage("正在中止导入…")

    @staticmethod
    def _library_import_summary(result) -> str:
        parts = [
            f"新增图片 {result.added_image_count} 张",
            f"为 {result.merged_tag_image_count} 张已有图片合并标签",
            f"新增标签 {result.added_tag_count} 个",
        ]
        if result.damaged_count:
            parts.append(f"跳过 {result.damaged_count} 张损坏图片")
        return "，".join(parts) + "。"

    def _refresh_after_library_import(self, result) -> None:
        if result.added_image_count or result.merged_tag_image_count:
            services.sticker_library_viewer_service.wiring.slot_refresh_content()
        if result.added_tag_count:
            self._window.customSearchBox.refresh_suggestions()

    def _close_library_import_progress_dialog(self):
        dialog = self._dialog
        self._dialog = None
        if dialog is not None:
            dialog.finish()
            dialog.deleteLater()

    def _finish_library_import(self):
        self._close_library_import_progress_dialog()
        self._window.set_write_actions_enabled(True)
        self._cancelling = False

    def _on_import_library_finished(self, result):
        self._finish_library_import()
        self._refresh_after_library_import(result)

        summary = self._library_import_summary(result)
        message = f"导入完成，{summary}"
        message += (
            "\n\n为了实现完整的搜索功能，请在数据库维护功能里"
            "按需重新进行OCR和生成图片特征索引。"
        )
        self._window.statusBar().showMessage(f"导入完成，{summary}", 8000)
        QMessageBox.information(self._window, "导入完成", message)

        if result.errors:
            details = "\n".join(result.errors[:10])
            remaining = len(result.errors) - 10
            if remaining > 0:
                details += f"\n另有 {remaining} 项未显示。"
            QMessageBox.warning(self._window, "部分图片损坏", details)

    def _on_import_library_cancelled(self, result):
        self._finish_library_import()
        self._refresh_after_library_import(result)

        message = f"导入已中止，{self._library_import_summary(result)}"
        message += (
            "中止过程中可能留下未引用的Blob文件，可通过数据库维护功能清理；"
            "已导入图片仍需在数据库维护里补做OCR和生成图片特征索引。"
        )
        self._window.statusBar().showMessage(message, 8000)
        QMessageBox.information(self._window, "导入已中止", message)

    def _on_import_library_failed(self, error_message: str):
        self._finish_library_import()
        self._window.statusBar().clearMessage()
        QMessageBox.critical(self._window, "导入失败", error_message)