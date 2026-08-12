#coding=utf-8
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QIcon, QStandardItemModel
from PyQt6.QtWidgets import QComboBox

import services.sticker_library_viewer_service
from utils.resource_path import resolve_resource_path

from .widgets.sticker_list_page import StickerListPage

logger = logging.getLogger(__name__)


class InfiniteStickerCollectionPage(StickerListPage):
    """全库浏览标签页：滚动到底部时增量加载。"""

    PAGE_SIZE = 100
    UI_FILE_NAME = "page_infinite_sticker_collection.ui"
    SORT_OPTIONS = (
        ("imported_at", True, "导入日期（新到旧）"),
        ("imported_at", False, "导入日期（旧到新）"),
        ("file_size", False, "文件大小（小到大）"),
        ("file_size", True, "文件大小（大到小）"),
    )

    def __init__(self, *, auto_refresh: bool = True):
        super().__init__(
            ui_file_name=self.UI_FILE_NAME,
            auto_refresh=auto_refresh,
        )
        self._sort_order_by = "imported_at"
        self._sort_descending = True
        self._page_size = self.PAGE_SIZE
        self._offset = 0
        self._has_more = True
        self._loading_more = False
        self.listViewStickerList.load_more_requested.connect(self._load_more)

        self.refresh_action = QAction(self)
        self.refresh_action.setObjectName("refreshAction")
        self.refresh_action.setIcon(
            QIcon(str(resolve_resource_path("refresh-cw.svg")))
        )
        self.refresh_action.setToolTip("刷新图库")
        self.refresh_action.triggered.connect(self._on_refresh_clicked)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toolbar.addAction(self.refresh_action)
        self._setup_sort_combo()

        if auto_refresh:
            self.signal_refresh_content.connect(
                services.sticker_library_viewer_service.wiring.slot_refresh_content
            )
            services.sticker_library_viewer_service.wiring.signal_refresh_library_content.connect(
                self._on_refresh_library_content
            )
            self.signal_refresh_content.emit()
        else:
            self._reset_and_load_first_page()
        self._setup_display_size_slider()

    def _setup_sort_combo(self) -> None:
        self.sort_combo = QComboBox(self)
        self.sort_combo.setObjectName("sortComboBox")
        self.sort_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.sort_combo.setToolTip("图片排序方式")
        self.sort_combo.setAccessibleName("图片排序方式")
        for order_by, descending, label in self.SORT_OPTIONS:
            self.sort_combo.addItem(label, (order_by, descending))
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self.add_toolbar_widget(self.sort_combo)

    def _on_sort_changed(self, index: int) -> None:
        if index < 0:
            return
        order_by, descending = self.sort_combo.itemData(index)
        self._sort_order_by = order_by
        self._sort_descending = descending
        self._reset_and_load_first_page()

    def _on_refresh_clicked(self) -> None:
        self.signal_refresh_content.emit()

    def _on_refresh_library_content(self) -> None:
        self._reset_and_load_first_page()

    def _reset_and_load_first_page(self) -> None:
        self._offset = 0
        self._has_more = True
        self._loading_more = False

        previous_model = self.listViewStickerList.model()
        new_model = QStandardItemModel(self.listViewStickerList)
        self.listViewStickerList.setModel(new_model)
        if previous_model is not None and previous_model is not new_model:
            previous_model.deleteLater()
        self.listViewStickerList.scrollToTop()
        self._load_more()

    def _load_more(self) -> None:
        if self._loading_more or not self._has_more:
            return

        self._loading_more = True
        try:
            images = services.sticker_library_viewer_service.load_library_page(
                offset=self._offset,
                count=self._page_size,
                order_by=self._sort_order_by,
                descending=self._sort_descending,
            )
            if not images:
                self._has_more = False
                return

            items = services.sticker_library_viewer_service.build_sticker_items(
                images
            )
            model = self.listViewStickerList.model()
            if model is None:
                model = QStandardItemModel(self.listViewStickerList)
                self.listViewStickerList.setModel(model)
            for item in items:
                model.appendRow(item)

            self._offset += len(images)
            if len(images) < self._page_size:
                self._has_more = False
        except Exception:
            logger.exception("加载图库分页失败")
            self._has_more = False
        finally:
            self._loading_more = False
