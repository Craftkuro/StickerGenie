# coding=utf-8

from .page_finite_sticker_collection import FiniteStickerCollectionPage


class SimilarImagesPage(FiniteStickerCollectionPage):
    """相似图片查找标签页：展示带相似度数据的有限结果集。"""

    def __init__(self, *, auto_refresh: bool = False):
        super().__init__(auto_refresh=auto_refresh)
