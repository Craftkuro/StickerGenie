# coding=utf-8

from .page_finite_sticker_collection import FiniteStickerCollectionPage


class SearchResultPage(FiniteStickerCollectionPage):
    """标签/文本/文件名搜索结果标签页：一次性加载全部匹配图片。"""

    def __init__(self, *, auto_refresh: bool = False):
        super().__init__(auto_refresh=auto_refresh)
