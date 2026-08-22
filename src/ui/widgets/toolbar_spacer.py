# coding=utf-8
from PyQt6.QtWidgets import (
    QSizePolicy,
    QWidget,
)


class ToolbarSpacer(QWidget):
    """QToolBar 中的弹性占位控件，把位于其右侧的控件推到工具栏末端。

    objectName 固定为 toolbarSpacer，可在 QSS 中按该名称单独设置样式。
    """

    OBJECT_NAME = "toolbarSpacer"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

    def set_expanding(self, enabled: bool) -> None:
        """关闭伸展后 spacer 宽度归零，让左侧的 Expanding 控件独占剩余空间。"""
        horizontal = (
            QSizePolicy.Policy.Expanding
            if enabled
            else QSizePolicy.Policy.Fixed
        )
        self.setSizePolicy(horizontal, QSizePolicy.Policy.Preferred)
