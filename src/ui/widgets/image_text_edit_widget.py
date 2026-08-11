# coding=utf-8
"""ImageTextEditWidget - 图片文字编辑组件"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QMessageBox,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from commons.dto import StickerImage

logger = logging.getLogger(__name__)


class ImageTextEditWidget(QWidget):
    """
    自定义图片文本编辑器。

    顶部为一个小型工具栏（目前只有保存按钮），下方为 QTextEdit。
    通过 set_sticker() 载入图片的 text_in_image 字段，点击保存后写入数据库。
    """

    def __init__(self, parent=None, database=None):
        super().__init__(parent)
        self._database = database
        self._sticker: Optional[StickerImage] = None

        self._setup_ui()
        self._update_editor_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("imageTextToolBar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        self.save_action = QAction("保存", self)
        self.save_action.setObjectName("saveTextAction")
        self.save_action.setToolTip("保存文本到图片记录")
        self.save_action.triggered.connect(self.save_text)
        self.toolbar.addAction(self.save_action)

        layout.addWidget(self.toolbar)

        self.text_edit = QTextEdit(self)
        self.text_edit.setObjectName("imageTextEdit")
        self.text_edit.setPlaceholderText("暂无图片文字，可在此编辑并保存")
        layout.addWidget(self.text_edit, 1)

    def set_database(self, database):
        """设置用于保存文本的数据库实例。"""
        self._database = database
        self._update_editor_state()

    def set_sticker(self, sticker: Optional[StickerImage]):
        """载入图片记录，并显示数据库中的 text_in_image 内容。"""
        self._sticker = sticker
        text = sticker.text_in_image or "" if sticker is not None else ""
        self.text_edit.setPlainText(text)
        self._update_editor_state()

    def save_text(self):
        """把编辑器当前内容写入当前图片的 text_in_image 字段。"""
        sticker = self._sticker
        database = self._database
        if sticker is None or database is None:
            return

        text = self.text_edit.toPlainText()
        try:
            database.set_sticker_texts({sticker.id: text})
        except (ValueError, OSError, SQLAlchemyError) as exc:
            logger.exception("保存图片文字失败，id=%s", sticker.id)
            QMessageBox.critical(self, "保存失败", str(exc))
            return

        sticker.text_in_image = text

    def _update_editor_state(self):
        enabled = self._sticker is not None and self._database is not None
        self.text_edit.setEnabled(enabled)
        self.save_action.setEnabled(enabled)
