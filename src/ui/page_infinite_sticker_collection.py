#coding=utf-8
import logging
from pathlib import Path

import apppath
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QIcon, QStandardItemModel

import services.sticker_library_viewer_service

from .widgets.sticker_list_page import StickerListPage

logger = logging.getLogger(__name__)


def _resolve_resource_path(filename: str) -> Path:
    if apppath.app_path is not None:
        return apppath.app_path / "resources" / filename
    return Path(__file__).resolve().parents[1] / "resources" / filename


class InfiniteStickerCollectionPage(StickerListPage):
    """全库浏览标签页：滚动到底部时增量加载。"""

    PAGE_SIZE = 100
    UI_FILE_NAME = "page_infinite_sticker_collection.ui"

    def __init__(self, *, auto_refresh: bool = True):
        super().__init__(
            ui_file_name=self.UI_FILE_NAME,
            auto_refresh=auto_refresh,
        )
        self._page_size = self.PAGE_SIZE
        self._offset = 0
        self._has_more = True
        self._loading_more = False
        self.listViewStickerList.load_more_requested.connect(self._load_more)

        self.refresh_action = QAction(self)
        self.refresh_action.setObjectName("refreshAction")
        self.refresh_action.setIcon(
            QIcon(str(_resolve_resource_path("refresh-cw.svg")))
        )
        self.refresh_action.setToolTip("刷新图库")
        self.refresh_action.triggered.connect(self._on_refresh_clicked)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toolbar.addAction(self.refresh_action)

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
