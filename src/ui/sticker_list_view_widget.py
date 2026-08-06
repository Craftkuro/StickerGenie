#coding=utf-8
import logging

from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QWidget, QListView

import commons.constants
#import commons.classes

logger = logging.getLogger(__name__)



class StickerListView(QListView):
    """
    通用的表情包列表视图
    """
    def __init__(self, model: QStandardItemModel):
        super().__init__()

        self.display_mode = commons.constants.LIST_DISPLAY_MODE_ICON
        self.sort_mode = commons.constants.SORT_BY_DATE
        self.reverse_sort = False

        self.setModel(model)
