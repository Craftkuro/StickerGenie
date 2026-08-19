# coding=utf-8
from collections.abc import Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QTextEdit

from commons.dto import StickerImage

from .page_finite_sticker_collection import FiniteStickerCollectionPage


class AdvancedSearchResultPage(FiniteStickerCollectionPage):
    """高级标签表达式结果页，提供表达式查看和复制。"""

    def __init__(
        self,
        expression: str,
        initial_images: Iterable[StickerImage],
        *,
        auto_refresh: bool = False,
    ):
        super().__init__(auto_refresh=auto_refresh)

        self.expression_label = QLabel("表达式", self)
        self.expression_label.setObjectName("expressionLabel")
        self.expression_text_edit = QTextEdit(self)
        self.expression_text_edit.setObjectName("expressionTextEdit")
        self.expression_text_edit.setPlainText(expression)
        self.expression_text_edit.setReadOnly(True)
        self.expression_text_edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.expression_text_edit.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth
        )
        self.expression_text_edit.setMinimumWidth(280)
        self.copy_button = QPushButton("复制", self)
        self.copy_button.setObjectName("copyButton")

        self._insert_toolbar_widget(self.expression_label)
        self._insert_toolbar_widget(self.expression_text_edit)
        self._insert_toolbar_widget(self.copy_button)

        self.copy_button.clicked.connect(self._copy_expression)

        self.refresh_content(self._build_sticker_model(initial_images))

    def _insert_toolbar_widget(self, widget):
        if not hasattr(self, "_expression_toolbar_anchor"):
            actions = self.toolbar.actions()
            self._expression_toolbar_anchor = actions[0] if actions else None

        if self._expression_toolbar_anchor is not None:
            return self.toolbar.insertWidget(
                self._expression_toolbar_anchor,
                widget,
            )
        return self.add_toolbar_widget(widget)

    @staticmethod
    def _build_sticker_model(images: Iterable[StickerImage]):
        # 延迟导入，避免结果页和 viewer service 的模块循环依赖。
        from services.sticker_library_viewer_service import build_sticker_model

        return build_sticker_model(images)

    def _copy_expression(self) -> None:
        QApplication.clipboard().setText(
            self.expression_text_edit.toPlainText()
        )
