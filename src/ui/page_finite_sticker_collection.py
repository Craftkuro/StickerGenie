#coding=utf-8

from .widgets.sticker_list_page import StickerListPage


class FiniteStickerCollectionPage(StickerListPage):
    """有限结果集合标签页基类：搜索结果、相似图片等一次性加载全部数据。"""

    UI_FILE_NAME = "page_finite_sticker_collection.ui"

    def __init__(self, *, auto_refresh: bool = False):
        super().__init__(
            ui_file_name=self.UI_FILE_NAME,
            auto_refresh=auto_refresh,
        )
