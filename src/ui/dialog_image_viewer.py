# coding=utf-8
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QMovie, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHeaderView,
    QMenu,
    QMessageBox,
    QTableWidgetItem,
)
from sqlalchemy.exc import SQLAlchemyError

import apppath
import services.global_instances
import services.image_clipboard_service
from commons.dto import StickerImage, Tag
from utils.save_as_files import save_as_files
from ui.dialog_tag_selector import TagSelectorDialog
from ui.widgets.custom_tag_widget import CustomTagWidget, TAG_ACCENT_COLOR_ROLE

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_TITLE = "图片查看器"
TAG_DATA_ROLE = Qt.ItemDataRole.UserRole


class ImageViewerDialog(QDialog):
    """
    图片查看器对话框。

    显示一张图片并随窗口大小缩放，完整展示图片内容。
    """

    def __init__(self, parent=None, database=None):
        super().__init__(parent)

        self._database = (
            database
            if database is not None
            else services.global_instances.current_library_db
        )
        self._sticker: Optional[StickerImage] = None
        self._movie: Optional[QMovie] = None
        self._file_path: Optional[str] = None
        self._display_name: str = ""

        ui_file_path = apppath.app_path / 'ui' / 'dialog_image_viewer.ui'
        uic.loadUi(ui_file_path, self)

        # 强制激活第1个标签，以免编辑ui文件的时候存入其他激活的标签影响程序运行时行为
        self.tabWidgetBottom.setCurrentIndex(0)

        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._init_text_editor()
        self._init_tag_editor()
        self.widgetTagEditor.hide()
        self._init_file_info_table()
        self._init_image_viewer()

    def _init_text_editor(self):
        self.imageTextEditWidget.set_database(self._database)

    def _init_tag_editor(self):
        self._tag_model = QStandardItemModel(self)
        self._tag_widget = CustomTagWidget(self._tag_model, self.widgetTagEditor)
        self._tag_widget.add_action.triggered.connect(self._add_tag)
        self._tag_widget.delete_action.triggered.connect(self._delete_selected_tags)
        self.widgetTagEditor.layout().addWidget(self._tag_widget)

    def _init_image_viewer(self):
        self._image_view = self.widgetImageViewer
        self._image_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._image_view.customContextMenuRequested.connect(
            self._show_image_context_menu
        )
        self.splitter.setSizes([320, 160])

    def _init_file_info_table(self):
        table = self.tableWidgetFileInfo
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["属性", "值"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def load_image(
        self,
        file_path: str,
        title: str = "",
        sticker: Optional[StickerImage] = None,
    ):
        """
        加载并显示图片。

        :param file_path: 图片文件路径
        :param title: 窗口标题中显示的图片名称，为空时使用默认标题
        :param sticker: 图片对应的 StickerImage DTO，用于编辑标签和图片文本
        """
        self._stop_movie()
        pixmap = QPixmap(file_path)
        if Path(file_path).suffix.lower() == ".gif":
            movie = QMovie(file_path)
            self._movie = movie
            self.widgetImageViewer.set_movie(movie)
            if not movie.isValid():
                self._movie = None
                self.widgetImageViewer.set_image(pixmap)
        else:
            self.widgetImageViewer.set_image(pixmap)
        if pixmap.isNull():
            logger.warning("无法加载图片: %s", file_path)

        if title:
            self.setWindowTitle(f"{title} - {DEFAULT_WINDOW_TITLE}")
        else:
            self.setWindowTitle(DEFAULT_WINDOW_TITLE)

        self._sticker = sticker
        self._file_path = file_path
        self._display_name = (
            getattr(self._sticker, "original_file_name", None)
            or title
            or Path(file_path).name
        )
        self.imageTextEditWidget.set_sticker(sticker)
        self.widgetTagEditor.setVisible(sticker is not None)
        self._reload_tag_model()
        self._reload_file_info(file_path, pixmap, title)

    def closeEvent(self, event):
        self._stop_movie()
        super().closeEvent(event)

    def _stop_movie(self) -> None:
        movie = self._movie
        if movie is not None:
            movie.stop()
            movie.setFileName("")
            movie.deleteLater()
            self._movie = None

    def _show_image_context_menu(self, position):
        if not self._file_path:
            return

        menu = QMenu(self)
        copy_action = menu.addAction("复制到剪贴板")
        copy_action.triggered.connect(
            lambda _checked=False: self._copy_current_image_to_clipboard()
        )
        if self._is_current_image_gif():
            copy_first_frame_action = menu.addAction("复制首帧到剪贴板")
            copy_first_frame_action.triggered.connect(
                lambda _checked=False: self._copy_current_image_to_clipboard(
                    anim_as_static_image=True
                )
            )
        save_as_action = menu.addAction("另存为")
        save_as_action.triggered.connect(
            lambda _checked=False: self._save_current_image_as()
        )
        menu.exec(self._image_view.viewport().mapToGlobal(position))

    def _is_current_image_gif(self) -> bool:
        if getattr(self._sticker, "extension", "").casefold() == ".gif":
            return True
        return Path(self._file_path or "").suffix.casefold() == ".gif"

    def _copy_current_image_to_clipboard(
        self,
        *,
        anim_as_static_image: bool = False,
    ) -> None:
        if not self._file_path or not self._display_name:
            return
        try:
            services.image_clipboard_service.copy_image_to_clipboard(
                self._file_path,
                self._display_name,
                anim_as_static_image=anim_as_static_image,
            )
        except Exception as exc:
            logger.exception("复制图片到剪贴板失败")
            QMessageBox.warning(self, "复制失败", str(exc))

    def _save_current_image_as(self) -> None:
        if not self._file_path or not self._display_name:
            return

        destination, _ = QFileDialog.getSaveFileName(
            self,
            "另存为",
            self._display_name,
        )
        if not destination:
            return

        target_path = Path(destination)
        _, failed = save_as_files(
            [(self._file_path, self._display_name)],
            target_path.parent,
            target_names=[target_path.name],
        )
        if failed:
            QMessageBox.warning(self, "另存为失败", "图片保存失败。")
        else:
            QMessageBox.information(self, "另存为成功", "图片已保存。")

    def _reload_file_info(self, file_path: str, pixmap: QPixmap, title: str):
        path = Path(file_path)
        try:
            display_path = str(path.resolve(strict=False))
        except OSError:
            display_path = str(path)

        try:
            stat = path.stat()
        except OSError:
            stat = None

        original_name = getattr(self._sticker, "original_file_name", None)
        file_name = original_name or title or path.name or "不可用"

        extension = path.suffix or getattr(self._sticker, "extension", "")
        file_format = extension.lstrip(".").upper() or "不可用"

        if not pixmap.isNull():
            dimensions = f"{pixmap.width()} x {pixmap.height()} 像素"
        else:
            width = getattr(self._sticker, "size_width", None)
            height = getattr(self._sticker, "size_height", None)
            dimensions = f"{width} x {height} 像素" if width and height else "不可用"

        file_size = stat.st_size if stat is not None else getattr(
            self._sticker, "file_size", None
        )
        modified_at = (
            datetime.fromtimestamp(stat.st_mtime)
            if stat is not None
            else getattr(self._sticker, "modification_date", None)
        )

        rows = [
            ("文件名", file_name),
            ("文件路径", display_path),
            ("文件格式", file_format),
            ("图片尺寸", dimensions),
            ("文件大小", self._format_file_size(file_size)),
            ("修改时间", self._format_datetime(modified_at)),
        ]

        imported_at = getattr(self._sticker, "imported_at", None)
        if imported_at is not None:
            rows.append(("导入时间", self._format_datetime(imported_at)))

        file_hash = getattr(self._sticker, "hash", None)
        if file_hash:
            rows.append(("SHA1", str(file_hash)))

        self.tableWidgetFileInfo.setRowCount(len(rows))
        for row, (label, value) in enumerate(rows):
            label_item = QTableWidgetItem(label)
            value_item = QTableWidgetItem(value)
            value_item.setToolTip(value)
            self.tableWidgetFileInfo.setItem(row, 0, label_item)
            self.tableWidgetFileInfo.setItem(row, 1, value_item)

            if label == "文件路径":
                line_height = self.tableWidgetFileInfo.fontMetrics().lineSpacing()
                self.tableWidgetFileInfo.setRowHeight(row, 3 * line_height)

    @staticmethod
    def _format_file_size(size: Optional[int]) -> str:
        if size is None or size < 0:
            return "不可用"
        if size < 1024:
            return f"{size:,} 字节"

        value = float(size)
        for unit in ("KB", "MB", "GB", "TB"):
            value /= 1024
            if value < 1024 or unit == "TB":
                return f"{value:.2f} {unit} ({size:,} 字节)"

        return f"{size:,} 字节"

    @staticmethod
    def _format_datetime(value: Optional[datetime]) -> str:
        if value is None:
            return "不可用"
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def _reload_tag_model(self):
        self._tag_model.clear()
        if self._sticker is None:
            return

        for tag in self._sticker.tags:
            item = QStandardItem(tag.name)
            item.setEditable(False)
            item.setData(tag, TAG_DATA_ROLE)
            item.setData(tag.color_rgb, TAG_ACCENT_COLOR_ROLE)
            self._tag_model.appendRow(item)

    def _add_tag(self):
        """打开标签选择对话框；确认后把所选标签追加到当前图片。"""
        if self._sticker is None:
            return

        dialog = TagSelectorDialog(
            database=self._database,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_tags(self._merge_selected_tags(dialog.selected_tags()))

    def _merge_selected_tags(self, selected_tags: list[Tag]) -> list[Tag]:
        """把所选标签追加到当前标签集合，按 id 去重并保持顺序。"""
        known_ids = {tag.id for tag in self._sticker.tags}
        merged = list(self._sticker.tags)
        for tag in selected_tags:
            if tag.id not in known_ids:
                known_ids.add(tag.id)
                merged.append(tag)
        return merged

    def _delete_selected_tags(self):
        if self._sticker is None:
            return

        selected_indexes = [
            index
            for index in self._tag_widget.selectedIndexes()
            if index.data(TAG_DATA_ROLE) is not None
        ]
        if not selected_indexes:
            QMessageBox.information(self, "删除标签", "请先选择要从当前图片移除的标签。")
            return

        selected_ids = {
            index.data(TAG_DATA_ROLE).id for index in selected_indexes
        }
        tag_names = "、".join(
            index.data(TAG_DATA_ROLE).name for index in selected_indexes
        )
        answer = QMessageBox.question(
            self,
            "删除标签",
            f'确实要取消关联标签"{tag_names}"吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        remaining_tags = [tag for tag in self._sticker.tags if tag.id not in selected_ids]
        self._save_tags(remaining_tags)

    def _save_tags(self, tags: list[Tag]):
        try:
            updated_sticker = self._database.set_sticker_tags(
                self._sticker.id,
                [tag.id for tag in tags],
            )
        except (ValueError, OSError, SQLAlchemyError) as exc:
            logger.exception("保存图片标签失败")
            QMessageBox.critical(self, "保存失败", str(exc))
            return

        self._sticker.tags = updated_sticker.tags
        self._reload_tag_model()
