# coding=utf-8
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QTableWidgetItem,
)
from sqlalchemy.exc import SQLAlchemyError

import apppath
import services.global_instances
from commons.dto import StickerImage, Tag
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

        ui_file_path = apppath.app_path / 'ui' / 'dialog_image_viewer.ui'
        uic.loadUi(ui_file_path, self)

        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._init_tag_editor()
        self.widgetTagEditor.hide()
        self._init_file_info_table()
        self._init_image_viewer()

    def _init_tag_editor(self):
        self._tag_model = QStandardItemModel(self)
        self._tag_widget = CustomTagWidget(self._tag_model, self.widgetTagEditor)
        self._tag_widget.add_action.triggered.connect(self._add_tag)
        self._tag_widget.delete_action.triggered.connect(self._delete_selected_tags)
        self.widgetTagEditor.layout().addWidget(self._tag_widget)

    def _init_image_viewer(self):
        self._image_view = self.widgetImageViewer

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
        :param sticker: 图片对应的 StickerImage DTO，用于编辑标签
        """
        pixmap = QPixmap(file_path)
        self.widgetImageViewer.set_image(pixmap)
        if pixmap.isNull():
            logger.warning("无法加载图片: %s", file_path)

        if title:
            self.setWindowTitle(f"{title} - {DEFAULT_WINDOW_TITLE}")
        else:
            self.setWindowTitle(DEFAULT_WINDOW_TITLE)

        self._sticker = sticker
        self.widgetTagEditor.setVisible(sticker is not None)
        self._reload_tag_model()
        self._reload_file_info(file_path, pixmap, title)

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
            rows.append(("文件哈希", str(file_hash)))

        self.tableWidgetFileInfo.setRowCount(len(rows))
        for row, (label, value) in enumerate(rows):
            label_item = QTableWidgetItem(label)
            value_item = QTableWidgetItem(value)
            value_item.setToolTip(value)
            self.tableWidgetFileInfo.setItem(row, 0, label_item)
            self.tableWidgetFileInfo.setItem(row, 1, value_item)

        self.tableWidgetFileInfo.resizeRowsToContents()

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
        if self._sticker is None:
            return

        try:
            all_tags = self._database.list_tags()
        except (OSError, SQLAlchemyError) as exc:
            logger.exception("加载全局标签失败")
            QMessageBox.critical(self, "加载失败", str(exc))
            return
        current_ids = {tag.id for tag in self._sticker.tags}
        enabled_tags = [tag for tag in all_tags if tag.enabled and tag.id not in current_ids]
        tag_by_name = {tag.name: tag for tag in all_tags}

        tag_name, accepted = QInputDialog.getItem(
            self,
            "添加标签",
            "选择已有标签或输入新标签名称：",
            [tag.name for tag in enabled_tags],
            0,
            True,
        )
        tag_name = tag_name.strip()
        if not accepted or not tag_name:
            return

        if any(tag.name == tag_name for tag in self._sticker.tags):
            return

        tag = tag_by_name.get(tag_name)
        if tag is None:
            tag = Tag()
            tag.name = tag_name
            needs_save = True
        elif not tag.enabled:
            tag.enabled = True
            needs_save = True
        else:
            needs_save = False

        if needs_save:
            try:
                tag = self._database.add_or_modify_tag(tag)
            except (OSError, SQLAlchemyError) as exc:
                logger.exception("新增或启用标签失败")
                QMessageBox.critical(self, "保存失败", str(exc))
                return

        self._save_tags([*self._sticker.tags, tag])

    def _delete_selected_tags(self):
        if self._sticker is None:
            return

        selected_ids = {
            index.data(TAG_DATA_ROLE).id
            for index in self._tag_widget.selectedIndexes()
            if index.data(TAG_DATA_ROLE) is not None
        }
        if not selected_ids:
            QMessageBox.information(self, "删除标签", "请先选择要从当前图片移除的标签。")
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
