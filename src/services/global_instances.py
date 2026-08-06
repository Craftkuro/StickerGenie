#coding=utf-8
from blob_storage import BlobStorage
from stickerdb.v1.sticker_db import StickerDBV1
from PyQt6.QtWidgets import QMainWindow

#已打开的数据库实例
current_library_db: StickerDBV1 | None = None

# 已打开的blob存储实例
current_blob_storage: BlobStorage | None = None

# 主窗体实例
main_window: QMainWindow | None = None