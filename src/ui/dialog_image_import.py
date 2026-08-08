# coding=utf-8
import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Optional

from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QListWidgetItem,
    QMessageBox,
)

import apppath
import services.global_instances
import services.import_images
from utils.image_metadata import get_image_metadata

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
IMAGE_FILE_FILTER = (
    "图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff "
    "*.heif *.heic *.avif);;所有文件 (*)"
)
FILE_PATH_ROLE = Qt.ItemDataRole.UserRole


class ImageImportDialog(QDialog):
    """Collect, deduplicate, and import image files in a two-step dialog."""

    SELECTION_PAGE = 0
    CONFIRMATION_PAGE = 1

    def __init__(
        self,
        parent=None,
        *,
        database=None,
        import_service: Optional[Callable[[list[str]], list[Any]]] = None,
    ):
        super().__init__(parent)

        ui_file_path = apppath.app_path / "ui" / "dialog_image_import.ui"
        uic.loadUi(ui_file_path, self)

        self._database = (
            database
            if database is not None
            else services.global_instances.current_library_db
        )
        self._import_service = import_service or services.import_images.import_images
        self._prepared_file_paths: list[str] = []
        self.imported_stickers: list[Any] = []

        self.setWindowTitle("导入图片")
        self.listWidget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._connect_signals()
        self.stackedWidget.setCurrentIndex(self.SELECTION_PAGE)
        self._sync_page_controls()
        self._sync_selection_controls()

    @property
    def selected_file_paths(self) -> list[str]:
        return [
            self.listWidget.item(row).data(FILE_PATH_ROLE)
            for row in range(self.listWidget.count())
        ]

    @property
    def prepared_file_paths(self) -> list[str]:
        return list(self._prepared_file_paths)

    def _connect_signals(self):
        self.pushButtonAddFiles.clicked.connect(self._add_files)
        self.pushButtonAddDirs.clicked.connect(self._add_directory)
        self.pushButtonRemoveSelected.clicked.connect(self._remove_selected)
        self.pushButtonClearAll.clicked.connect(self._clear_files)
        self.pushButtonPrev.clicked.connect(self._show_selection_page)
        self.pushButtonNext.clicked.connect(self._show_confirmation_page)
        self.pushButtonOk.clicked.connect(self._start_import)
        self.pushButtonCancel.clicked.connect(self.reject)
        self.listWidget.itemSelectionChanged.connect(self._sync_selection_controls)
        self.stackedWidget.currentChanged.connect(self._sync_page_controls)

    def _add_files(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要导入的图片",
            "",
            IMAGE_FILE_FILTER,
        )
        self._add_paths(file_paths)

    def _add_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "选择图片目录", "")
        if not directory:
            return

        try:
            file_paths = sorted(
                (
                    path
                    for path in Path(directory).rglob("*")
                    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
                ),
                key=lambda path: str(path).casefold(),
            )
        except OSError as exc:
            logger.exception("扫描图片目录失败: %s", directory)
            QMessageBox.critical(self, "读取目录失败", str(exc))
            return

        self._add_paths(file_paths)

    def _add_paths(self, file_paths: Iterable[str | Path]):
        known_paths = {
            self._path_key(path) for path in self.selected_file_paths
        }

        for file_path in file_paths:
            normalized_path = self._normalize_path(file_path)
            path_key = self._path_key(normalized_path)
            if path_key in known_paths:
                continue

            item = QListWidgetItem(normalized_path)
            item.setData(FILE_PATH_ROLE, normalized_path)
            item.setToolTip(normalized_path)
            self.listWidget.addItem(item)
            known_paths.add(path_key)

        self._invalidate_prepared_files()
        self._sync_selection_controls()

    def _remove_selected(self):
        selected_rows = sorted(
            (self.listWidget.row(item) for item in self.listWidget.selectedItems()),
            reverse=True,
        )
        for row in selected_rows:
            self.listWidget.takeItem(row)

        self._invalidate_prepared_files()
        self._sync_selection_controls()

    def _clear_files(self):
        self.listWidget.clear()
        self._invalidate_prepared_files()
        self._sync_selection_controls()

    def _show_selection_page(self):
        self.stackedWidget.setCurrentIndex(self.SELECTION_PAGE)

    def _show_confirmation_page(self):
        try:
            prepared_paths, duplicate_count, invalid_count = self._prepare_files()
        except Exception as exc:
            logger.exception("准备待导入图片失败")
            QMessageBox.critical(self, "准备导入失败", str(exc))
            return

        self._prepared_file_paths = prepared_paths
        self.textEditNonDuplicateFiles.setPlainText("\n".join(prepared_paths))

        details = []
        if duplicate_count:
            details.append(f"已排除 {duplicate_count} 个重复文件")
        if invalid_count:
            details.append(f"已排除 {invalid_count} 个无效文件")
        detail_text = f"（{'，'.join(details)}）" if details else ""
        self.labelNonDuplicateFilesCount.setText(
            f"已选择 {len(prepared_paths)} 个不重复的文件{detail_text}。"
            '点击"确定"开始导入。'
        )
        self.stackedWidget.setCurrentIndex(self.CONFIRMATION_PAGE)

    def _prepare_files(self) -> tuple[list[str], int, int]:
        existing_hashes = self._load_existing_hashes()
        seen_hashes = set(existing_hashes)
        prepared_paths = []
        duplicate_count = 0
        invalid_count = 0

        for file_path in self.selected_file_paths:
            try:
                metadata = get_image_metadata(file_path)
            except (FileNotFoundError, OSError, ValueError):
                logger.warning("跳过无法读取的图片: %s", file_path)
                invalid_count += 1
                continue

            if metadata.hash in seen_hashes:
                duplicate_count += 1
                continue

            seen_hashes.add(metadata.hash)
            prepared_paths.append(file_path)

        return prepared_paths, duplicate_count, invalid_count

    def _load_existing_hashes(self) -> set[str]:
        if self._database is None:
            return set()

        return {
            sticker.hash
            for sticker in self._database.list_stickers(count=None)
            if getattr(sticker, "hash", None)
        }

    def _start_import(self):
        if not self._prepared_file_paths:
            return

        self._set_import_controls_enabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            imported = self._import_service(list(self._prepared_file_paths))
        except Exception as exc:
            logger.exception("导入图片失败")
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self._set_import_controls_enabled(True)

        self.imported_stickers = list(imported or [])
        self.accept()

    def _set_import_controls_enabled(self, enabled: bool):
        self.pushButtonPrev.setEnabled(enabled)
        self.pushButtonOk.setEnabled(enabled and bool(self._prepared_file_paths))
        self.pushButtonCancel.setEnabled(enabled)

    def _invalidate_prepared_files(self):
        self._prepared_file_paths = []
        self.textEditNonDuplicateFiles.clear()

    def _sync_page_controls(self):
        is_selection_page = (
            self.stackedWidget.currentIndex() == self.SELECTION_PAGE
        )
        self.pushButtonPrev.setVisible(not is_selection_page)
        self.pushButtonNext.setVisible(is_selection_page)
        self.pushButtonOk.setVisible(not is_selection_page)

        self.pushButtonNext.setEnabled(
            is_selection_page and self.listWidget.count() > 0
        )
        self.pushButtonOk.setEnabled(
            not is_selection_page and bool(self._prepared_file_paths)
        )

        self.pushButtonNext.setDefault(False)
        self.pushButtonOk.setDefault(False)
        primary_button = (
            self.pushButtonNext if is_selection_page else self.pushButtonOk
        )
        primary_button.setDefault(True)

    def _sync_selection_controls(self):
        has_files = self.listWidget.count() > 0
        self.pushButtonRemoveSelected.setEnabled(bool(self.listWidget.selectedItems()))
        self.pushButtonClearAll.setEnabled(has_files)
        if self.stackedWidget.currentIndex() == self.SELECTION_PAGE:
            self.pushButtonNext.setEnabled(has_files)

    @staticmethod
    def _normalize_path(file_path: str | Path) -> str:
        try:
            return str(Path(file_path).resolve(strict=False))
        except OSError:
            return str(Path(file_path).absolute())

    @staticmethod
    def _path_key(file_path: str | Path) -> str:
        return os.path.normcase(os.path.normpath(str(file_path)))
