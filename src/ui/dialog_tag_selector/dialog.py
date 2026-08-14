"""标签选择对话框 TagSelectorDialog。

将 TagSelectorWidget 包装为模态对话框；通过 selected_tag_ids 预置已选标签，
点击组件内的“确定”按钮接受对话框，之后通过 selected_tag_ids()/selected_tags()
获取结果。
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget

from commons.dto import Tag

from .matcher import TagSearchMatcher
from .widget import TagSelectorWidget


class TagSelectorDialog(QDialog):
    """将 TagSelectorWidget 包装为模态对话框。

    通过 selected_tag_ids 预置已选标签；点击组件内的“确定”按钮接受对话框，
    之后通过 selected_tag_ids()/selected_tags() 获取结果。
    """

    def __init__(
        self,
        database=None,
        *,
        selected_tag_ids: Iterable[int] = (),
        matcher: Optional[TagSearchMatcher] = None,
        parent: Optional[QWidget] = None,
        window_title: str = "选择标签",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.setMinimumSize(640, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._selector = TagSelectorWidget(
            database=database,
            selected_tag_ids=selected_tag_ids,
            matcher=matcher,
            parent=self,
        )
        layout.addWidget(self._selector)
        self._selector.ok_clicked.connect(self.accept)

    @property
    def selector(self) -> TagSelectorWidget:
        """内部标签选择组件。"""
        return self._selector

    def selected_tag_ids(self) -> List[int]:
        """按右侧列表显示顺序返回选中的标签 id。"""
        return self._selector.selected_tag_ids()

    def selected_tags(self) -> List[Tag]:
        """按右侧列表显示顺序返回选中的标签。"""
        return self._selector.selected_tags()
