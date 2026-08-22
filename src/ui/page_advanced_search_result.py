# coding=utf-8
from collections.abc import Iterable

from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
)

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
        self.expression_text_edit = QLineEdit(self)
        self.expression_text_edit.setObjectName("expressionTextEdit")
        self.expression_text_edit.setText(expression)
        self.expression_text_edit.setReadOnly(True)
        self.expression_text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.expression_text_edit.setMinimumWidth(280)
        self.copy_button = QPushButton("复制", self)
        self.copy_button.setObjectName("copyButton")

        # 表达式输入框独占工具栏剩余空间，因此关闭 spacer 的伸展。
        self.toolbar_spacer.set_expanding(False)
        self.insert_toolbar_widget_left_of_spacer(self.expression_label)
        self.insert_toolbar_widget_left_of_spacer(self.expression_text_edit)
        self.insert_toolbar_widget_left_of_spacer(self.copy_button)

        self.copy_button.clicked.connect(self._copy_expression)

        self.refresh_content(self._build_sticker_model(initial_images))

    @staticmethod
    def _build_sticker_model(images: Iterable[StickerImage]):
        # 延迟导入，避免结果页和 viewer service 的模块循环依赖。
        from services.sticker_library_viewer_service import build_sticker_model

        return build_sticker_model(images)

    def _copy_expression(self) -> None:
        QApplication.clipboard().setText(
            self.expression_text_edit.text()
        )
