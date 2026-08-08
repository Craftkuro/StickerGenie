# coding=utf-8
import logging
from typing import Optional

from PyQt6 import uic
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QMessageBox,
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

        self._init_tag_editor()
        self.widgetTagEditor.hide()
        self._init_image_viewer()

    def _init_tag_editor(self):
        self._tag_model = QStandardItemModel(self)
        self._tag_widget = CustomTagWidget(self._tag_model, self.widgetTagEditor)
        self._tag_widget.add_action.triggered.connect(self._add_tag)
        self._tag_widget.delete_action.triggered.connect(self._delete_selected_tags)
        self.widgetTagEditor.layout().addWidget(self._tag_widget)

    def _init_image_viewer(self):
        self._scene = QGraphicsScene(self)
        self._image_item = QGraphicsPixmapItem()
        self._scene.addItem(self._image_item)

        self._image_view = QGraphicsView(self._scene, self.widgetImageViewer)
        self._image_view.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self._image_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.widgetImageViewer.layout().addWidget(self._image_view)

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
        self._image_item.setPixmap(pixmap)
        if pixmap.isNull():
            logger.warning("无法加载图片: %s", file_path)

        if title:
            self.setWindowTitle(f"{title} - {DEFAULT_WINDOW_TITLE}")
        else:
            self.setWindowTitle(DEFAULT_WINDOW_TITLE)

        self._sticker = sticker
        self.widgetTagEditor.setVisible(sticker is not None)
        self._reload_tag_model()

        # 等窗口完成布局后再把图片适配到视图大小
        QTimer.singleShot(0, self._fit_image)

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_image()

    def _fit_image(self):
        if self._image_item.pixmap().isNull():
            return
        self._image_view.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
