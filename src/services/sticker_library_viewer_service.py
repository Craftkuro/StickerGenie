#coding=utf-8
import pathlib

from PyQt6.QtCore import pyqtSignal, pyqtSlot, QObject, Qt
from PyQt6.QtGui import QStandardItemModel, QIcon, QStandardItem

import services.global_instances
from blob_storage import BlobFileEntity
from commons.signal_objects import MainWindowNewTabRequest

from ui.page_sticker_library_view import StickerLibraryViewPage

# QStandardItem 自定义数据角色
# 存放图片在 blob 存储中的实际文件路径
ROLE_FILE_PATH = Qt.ItemDataRole.UserRole
# 存放图片对应的 StickerImage DTO（含 id，便于后续按 id 查询）
ROLE_STICKER_IMAGE = Qt.ItemDataRole.UserRole + 1

class Wiring(QObject):
    signal_refresh_library_content_result = pyqtSignal(QStandardItemModel)
    def __init__(self):
        super().__init__()

    @pyqtSlot()
    def slot_refresh_content(self):
        ret = refresh_content()
        self.signal_refresh_library_content_result.emit(ret)


wiring = Wiring()

######################################

def refresh_content() -> QStandardItemModel:
    db = services.global_instances.current_library_db
    images = db.list_stickers()
    model = QStandardItemModel()
    current_blob_storage = services.global_instances.current_blob_storage


    for image in images:
        file_path = current_blob_storage.read_file(BlobFileEntity(image.hash, image.extension))
        icon = QIcon(pathlib.Path(file_path).as_posix())
        item = QStandardItem(icon, image.original_file_name)
        item.setData(file_path, ROLE_FILE_PATH)
        item.setData(image, ROLE_STICKER_IMAGE)
        model.insertRow(model.rowCount(), item)

    return model

def open_sticker_library_view_tab():

    page = StickerLibraryViewPage()
    main_window = services.global_instances.main_window

    _signal = MainWindowNewTabRequest(page, "图库浏览")
    main_window.signal_add_new_tab.emit(_signal)

