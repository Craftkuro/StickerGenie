# coding=utf-8
import logging
import os
from collections.abc import Iterable
from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QListWidgetItem,
    QMessageBox,
)

import apppath
from commons.signal_objects import ImportImagesRequest

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
SOURCE_TYPE_ROLE = Qt.ItemDataRole.UserRole + 1
FILE_SOURCE = "file"
DIRECTORY_SOURCE = "directory"


class ImageImportDialog(QDialog):
    """Collect, deduplicate, and import image files in a two-step dialog."""

    signal_import_requested = pyqtSignal(ImportImagesRequest)

    SELECTION_PAGE = 0
    CONFIRMATION_PAGE = 1

    def __init__(self, parent=None):
        super().__init__(parent)

        ui_file_path = apppath.app_path / "ui" / "dialog_image_import.ui"
        uic.loadUi(ui_file_path, self)

        self._prepared_file_paths: list[str] = []

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
        self.pushButtonOk.clicked.connect(self._send_import_request)
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

        self._add_paths([directory], source_type=DIRECTORY_SOURCE)

    def _add_paths(
        self,
        file_paths: Iterable[str | Path],
        *,
        source_type: str = FILE_SOURCE,
    ):
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
            item.setData(SOURCE_TYPE_ROLE, source_type)
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
            self._prepared_file_paths = self._prepare_file_paths()
        except OSError as exc:
            logger.exception("扫描图片目录失败")
            QMessageBox.critical(self, "读取目录失败", str(exc))
            return

        self.textEditNonDuplicateFiles.setPlainText(
            "\n".join(self._prepared_file_paths)
        )
        self.labelNonDuplicateFilesCount.setText(
            f"已选择 {len(self._prepared_file_paths)} 个文件。"
            '点击"确定"开始导入。'
        )
        self.stackedWidget.setCurrentIndex(self.CONFIRMATION_PAGE)

    def _prepare_file_paths(self) -> list[str]:
        prepared_paths: list[str] = []
        known_paths: set[str] = set()

        for row in range(self.listWidget.count()):
            item = self.listWidget.item(row)
            source_path = item.data(FILE_PATH_ROLE)
            if item.data(SOURCE_TYPE_ROLE) == DIRECTORY_SOURCE:
                candidates: Iterable[str | Path] = sorted(
                    (
                        path
                        for path in Path(source_path).rglob("*")
                        if path.is_file()
                        and path.suffix.lower() in IMAGE_EXTENSIONS
                    ),
                    key=lambda path: str(path).casefold(),
                )
            else:
                candidates = (source_path,)

            for candidate in candidates:
                normalized_path = self._normalize_path(candidate)
                path_key = self._path_key(normalized_path)
                if path_key in known_paths:
                    continue

                prepared_paths.append(normalized_path)
                known_paths.add(path_key)

        return prepared_paths

    def _send_import_request(self):
        if not self._prepared_file_paths:
            return

        request = ImportImagesRequest(
            file_paths=tuple(self._prepared_file_paths),
            generate_vectors=self.checkBoxDoVectorGeneration.isChecked(),
            extract_text=self.checkBoxDoTextExtraction.isChecked(),
        )
        self.accept()
        self.signal_import_requested.emit(request)

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
        return os.path.abspath(os.path.normpath(str(file_path)))

    @staticmethod
    def _path_key(file_path: str | Path) -> str:
        return os.path.normcase(os.path.normpath(str(file_path)))
